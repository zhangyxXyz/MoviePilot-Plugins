"""Capacity cleanup with alternative cumulative seed-time or inactivity criteria.

All qBittorrent tasks are inspected, including tasks without the brush tag.
Missing metadata fails closed. No background tagging race is used for protection.
"""
import math
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

GIB = 1024 ** 3
REVISION = "20261008.5"


def tags(torrent):
    return {v.strip() for v in str(torrent.get("tags", "")).split(",") if v.strip()}


def number(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Missing or invalid numeric metadata")
    return value



def optional_number(value):
    try:
        return number(value)
    except (ValueError, TypeError):
        return None


def match_rules(torrent, complete, required_hours, inactive_hours, now, policy,
                is_hr=False, promotion_expiry=None):
    """Original conditions remain alternatives, HR has its own time/ratio branch."""
    seed = optional_number(torrent.get("seeding_time"))
    ratio = optional_number(torrent.get("ratio"))
    ratio_target = policy.get("seed_ratio", 0)
    if is_hr:
        rules = []
        if complete and policy.get("hr_seed_time", 0) and seed is not None and seed >= policy["hr_seed_time"] * 3600:
            rules.append(f"H&R 累计做种达到 {policy['hr_seed_time']:g} 小时")
        if complete and ratio_target and ratio is not None and ratio >= ratio_target:
            rules.append(f"H&R 分享率达到 {ratio_target:g}")
        return rules
    rules = []
    added = optional_number(torrent.get("added_on"))
    age = now - added if added is not None and 0 < added <= now else None
    if not complete:
        # Never use completed-seed conditions to delete an unfinished transfer.
        if policy.get("download_time", 0) and age is not None and age >= policy["download_time"] * 3600:
            rules.append(f"下载耗时达到 {policy['download_time']:g} 小时")
        expiry = optional_number(promotion_expiry)
        if policy.get("del_no_free") and expiry is not None and 0 < expiry <= now:
            rules.append("促销已过期且下载未完成")
        return rules
    if required_hours > 0 and seed is not None and seed >= required_hours * 3600:
        rules.append(f"累计做种达到 {required_hours:g} 小时")
    if inactive_hours > 0:
        last = optional_number(torrent.get("last_activity"))
        done = optional_number(torrent.get("completion_on"))
        if last is not None and last > 0 and done is not None:
            last = max(last, done)
            no_transfer = optional_number(torrent.get("upspeed")) == 0 and optional_number(torrent.get("dlspeed")) == 0
            if last <= now and now - last >= inactive_hours * 3600 and no_transfer:
                rules.append(f"连续未活动达到 {inactive_hours:g} 小时")
    if ratio_target and ratio is not None and ratio >= ratio_target:
        rules.append(f"分享率达到 {ratio_target:g}")
    uploaded = optional_number(torrent.get("uploaded"))
    if policy.get("seed_size", 0) and uploaded is not None and uploaded >= policy["seed_size"] * GIB:
        rules.append(f"上传量达到 {policy['seed_size']:g} GB")
    if (policy.get("seed_avgspeed", 0) and uploaded is not None and age and age > 0
            and seed is not None and seed >= 1800 and uploaded / age <= policy["seed_avgspeed"] * 1024):
        rules.append(f"平均上传速度低于 {policy['seed_avgspeed']:g} KB/s")
    return rules


POLICY_FIELDS = ("seed_ratio", "seed_size", "download_time", "seed_avgspeed", "hr_seed_time", "del_no_free")


def resolve_task_policy(plugin, task):
    """Resolve each field independently. Explicit false overrides; empty inherits."""
    def resolve(field, global_field=None):
        value = getattr(task, field, None)
        return getattr(plugin, "_global_" + (global_field or field), None) if value is None or value == "" else value
    policy = {key: resolve(key) for key in POLICY_FIELDS}
    for key in POLICY_FIELDS[:-1]:
        policy[key] = number(policy[key] or 0)
    policy["del_no_free"] = bool(policy["del_no_free"])
    hours = getattr(task, "min_seed_hours", None)
    if hours is None:
        hours = getattr(task, "seed_time", None)
    policy["min_seed_hours"] = number(hours if hours is not None else getattr(plugin, "_global_min_seed_hours", None) or 0)
    minutes = getattr(task, "seed_inactivetime", None)
    policy["inactive_hours"] = number(minutes) / 60 if minutes is not None else number(getattr(plugin, "_global_inactive_hours", None) or 0)
    policy["protected_tags"] = {v.strip() for v in str(resolve("delete_except_tags") or "").split(",") if v.strip()}
    return policy


def policies_by_hash(plugin, downloader_name):
    """Each recorded owner must pass its own effective policy before shared deletion."""
    result = defaultdict(list)
    for task in plugin._task_configs.values():
        if task.downloader == downloader_name:
            policy = resolve_task_policy(plugin, task)
            for h in (plugin._get_task_data(task.id, "torrents") or {}):
                result[h].append(policy)
    return dict(result)


def build_plan(rows, files, min_hours, protected_hashes=(), protected_tags=(),
               required_tags=("刷流",), min_hours_by_hash=None, inactive_hours=0, now=None, policy=None, hr_hashes=(), promotion_expiries=None, task_policies=None):
    """Pure planner. files maps hash to [{path, size, valid}], in canonical paths."""
    task_policies = task_policies or {}
    policy = dict(policy or {})
    for key in ("seed_ratio", "seed_size", "download_time", "seed_avgspeed", "hr_seed_time"):
        policy[key] = number(policy.get(key) or 0)
    hr_hashes = set(hr_hashes)
    promotion_expiries = promotion_expiries or {}
    required_tags, protected_tags = set(required_tags), set(protected_tags)
    protected_hashes = set(protected_hashes)
    min_hours = number(min_hours)
    inactive_hours = number(inactive_hours)
    now = number(time.time() if now is None else now)
    min_hours_by_hash = {h: number(v) for h, v in (min_hours_by_hash or {}).items()}
    if not required_tags or (min_hours <= 0 and not min_hours_by_hash and inactive_hours <= 0 and not any(policy.values()) and not task_policies):
        raise ValueError("At least one positive deletion criterion and selection tags are required")
    by_hash = {t["hash"]: t for t in rows}
    parent = {h: h for h in by_hash}

    def find(h):
        while parent[h] != h:
            parent[h] = parent[parent[h]]
            h = parent[h]
        return h

    owners, sizes = defaultdict(set), {}
    brush_hashes, brush_paths = set(), set()
    for h, t in by_hash.items():
        if not files.get(h):
            raise ValueError("Cannot establish shared-file ownership without full manifests")
        selected = required_tags.issubset(tags(t))
        if selected:
            brush_hashes.add(h)
        for f in files[h]:
            p, size = f["path"], int(number(f["size"]))
            if p in sizes and sizes[p] != size:
                raise ValueError("Conflicting sizes for a shared path")
            sizes[p] = size
            owners[p].add(h)
            if selected:
                brush_paths.add(p)
    for hashes in owners.values():
        hashes = list(hashes)
        for h in hashes[1:]:
            parent[find(h)] = find(hashes[0])
    groups = defaultdict(set)
    for h in by_hash:
        groups[find(h)].add(h)
    eligible, skipped = [], Counter()
    for hashes in groups.values():
        if not hashes.intersection(brush_hashes):
            continue
        reason = None
        seed_times = []
        matched_rules = {}
        for h in hashes:
            t = by_hash[h]
            effective = task_policies.get(h) or [{**policy, "min_seed_hours": min_hours_by_hash.get(h, min_hours),
                                                     "inactive_hours": inactive_hours, "protected_tags": protected_tags}]
            if h in protected_hashes or any(tags(t).intersection(p.get("protected_tags", protected_tags)) for p in effective):
                reason = "H&R或保护标签"
                break
            try:
                progress, left = number(t.get("progress")), number(t.get("amount_left"))
                complete = progress >= 1 and left == 0 and number(t.get("completion_on")) > 0
                incomplete = progress < 1 and left > 0
            except (ValueError, TypeError):
                reason = "缺少完成信息"
                break
            state = str(t.get("state", "")).lower()
            valid_state = (complete and state in {"uploading", "stalledup", "queuedup", "pausedup", "stoppedup", "forcedup"}) or (
                incomplete and state in {"downloading", "stalleddl", "queueddl", "pauseddl", "stoppeddl", "forceddl"})
            if not valid_state:
                reason = "任务状态异常或完成信息不一致"
                break
            if not all(f.get("valid", False) or (incomplete and f.get("path_safe", False)) for f in files[h]):
                reason = "文件状态或路径无法验证"
                break
            required_hours = max(p["min_seed_hours"] for p in effective)
            seed_time = optional_number(t.get("seeding_time")) or 0
            owner_rules = [match_rules(t, complete, p["min_seed_hours"], p["inactive_hours"], now, p,
                                       h in hr_hashes, promotion_expiries.get(h)) for p in effective]
            rules = sorted({rule for matches in owner_rules for rule in matches}) if all(owner_rules) else []
            if not rules:
                reason = "自身或关联辅种未满足清理条件"
                break
            matched_rules[h] = rules
            seed_times.append(seed_time)
        if reason:
            skipped[reason] += 1
            continue
        all_paths = {f["path"] for h in hashes for f in files[h]}
        accounted_paths = all_paths.intersection(brush_paths)
        eligible.append({
            "hashes": sorted(hashes), "names": [by_hash[h]["name"] for h in sorted(hashes)],
            "bytes": sum(sizes[p] for p in accounted_paths),
            "paths": sorted(all_paths), "seed_seconds": min(seed_times),
            "matched_rules": matched_rules,
            "required_hours": {h: max(p["min_seed_hours"] for p in task_policies[h]) if task_policies.get(h) else min_hours_by_hash.get(h, min_hours) for h in sorted(hashes)},
        })
    eligible.sort(key=lambda g: (-g["seed_seconds"], g["hashes"]))
    return {"total_bytes": sum(sizes[p] for p in brush_paths), "groups": eligible,
            "skipped": dict(skipped), "brush_count": len(brush_hashes)}


def snapshot(qbc):
    """Read all manifests in one downloader's path namespace, including untagged reseeds."""
    rows = list(qbc.torrents_info())
    manifests = {}
    for t in rows:
        h = t["hash"]
        manifest = list(qbc.torrents_files(torrent_hash=h))
        if not manifest:
            raise ValueError("Cannot read complete file manifest")
        save_path = t.get("save_path")
        if not save_path or not os.path.isabs(save_path):
            raise ValueError("Missing absolute save path")
        manifests[h] = []
        for f in manifest:
            name = f["name"]
            if os.path.isabs(name) or ".." in Path(name).parts:
                raise ValueError("Unsafe torrent file path")
            canonical = os.path.normpath(os.path.join(save_path, name))
            manifests[h].append({"path": canonical, "size": f["size"],
                                 "valid": number(f.get("progress")) >= 1, "path_safe": True})
    # Unknown newly added tasks cannot be omitted from shared-file ownership.
    fresh = list(qbc.torrents_info())
    signature = lambda ts: {(t["hash"], t.get("save_path"), t.get("content_path")) for t in ts}
    if signature(fresh) != signature(rows):
        raise RuntimeError("Task ownership changed during manifest collection")
    return fresh, manifests


def protected_brush_hashes(plugin, downloader_name):
    protected, managed = set(), set()
    for task in plugin._task_configs.values():
        if task.downloader != downloader_name:
            continue
        records = plugin._get_task_data(task.id, "torrents") or {}
        managed.update(records)
        for h, record in records.items():
            if not task.enabled or not task.proxy_delete:
                protected.add(h)
    return protected, managed



def minimum_hours_by_hash(plugin, downloader_name):
    """Resolve task ownership from persisted records; overlapping owners take the maximum."""
    result = {}
    for task in plugin._task_configs.values():
        if task.downloader != downloader_name:
            continue
        value = getattr(task, "min_seed_hours", None)
        if value is None or value == "":
            continue
        hours = number(value)
        if hours <= 0:
            continue
        for h in (plugin._get_task_data(task.id, "torrents") or {}):
            result[h] = max(result.get(h, 0), hours)
    return result



def policy_metadata(plugin, downloader_name):
    hr_hashes, expiry_values = set(), defaultdict(list)
    for task in plugin._task_configs.values():
        if task.downloader != downloader_name:
            continue
        for h, record in (plugin._get_task_data(task.id, "torrents") or {}).items():
            if record.get("hit_and_run") or task.site_hr_active:
                hr_hashes.add(h)
            # All recorded owners must have a known expiry, then use the latest one.
            expiry = None
            if resolve_task_policy(plugin, task)["del_no_free"]:
                parsed = plugin._promotion_expiry_at(record.get("freedate"), task.timezone_offset)
                expiry = parsed.timestamp() if parsed else None
            expiry_values[h].append(expiry)
    expiries = {h: max(values) for h, values in expiry_values.items() if values and all(v is not None for v in values)}
    return hr_hashes, expiries


def execute(plugin):
    """Run one capacity cycle; this is the only deletion path in guarded mode."""
    from app.sdk.logging import logger
    from app.sdk.services import DownloaderHelper
    min_hours = number(plugin._global_min_seed_hours or 0)
    inactive_hours = number(getattr(plugin, "_global_inactive_hours", None) or 0)
    limits = [number(v) for v in str(plugin._global_delete_size_range).split("-")]
    low, high = limits[0], limits[-1]
    if not 0 < low < high:
        raise ValueError("Invalid capacity/minimum-time settings")
    enabled_tasks = [t for t in plugin._task_configs.values() if t.enabled]
    downloader_names = {t.downloader for t in enabled_tasks}
    if len(downloader_names) != 1:
        raise ValueError("Minimum-time shared-file protection currently requires a single downloader")
    downloader_name = next(iter(downloader_names))
    required = {"刷流"}
    protected_tags = {v.strip() for v in str(getattr(plugin, "_global_delete_except_tags", "MOVIEPILOT,H&R") or "").split(",") if v.strip()}
    policy = {key: getattr(plugin, f"_global_{key}", None) for key in
              ("seed_ratio", "seed_size", "download_time", "seed_avgspeed", "hr_seed_time", "del_no_free")}
    service = DownloaderHelper().get_service(name=downloader_name)
    if not service or not getattr(service.instance, "qbc", None):
        raise RuntimeError("Guarded cleanup requires an available qBittorrent downloader")
    qbc = service.instance.qbc

    def current():
        rows, files = snapshot(qbc)
        protected, managed = protected_brush_hashes(plugin, downloader_name)
        protected.update(t["hash"] for t in rows if "刷流" in tags(t) and t["hash"] not in managed)
        hr_hashes, expiries = policy_metadata(plugin, downloader_name)
        return build_plan(rows, files, min_hours, protected, protected_tags, required,
                          task_policies=policies_by_hash(plugin, downloader_name),
                          inactive_hours=inactive_hours, policy=policy,
                          hr_hashes=hr_hashes, promotion_expiries=expiries)

    plan = current()
    report = {"revision": REVISION, "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "before_gb": round(plan["total_bytes"] / GIB, 3), "min_hours": min_hours,
              "inactive_hours": inactive_hours, "condition_mode": "any", "policy": policy,
              "high_gb": high, "low_gb": low, "deleted": [], "skipped": plan["skipped"]}
    plugin.save_data("guard_last_run", report)
    if plan["total_bytes"] < high * GIB:
        report.update(status="below_threshold", after_gb=report["before_gb"])
        plugin.save_data("guard_last_run", report)
        logger.info(f"保种保护清理：{report['before_gb']} GB，未达到 {high} GB，不删除")
        return 0
    logger.info(f"保种保护清理启动：{report['before_gb']} GB，累计做种 {min_hours} 小时或未活动 {inactive_hours} 小时，目标 {low} GB")
    for _ in range(len(plan["groups"]) + 1):
        if not plugin._enabled:
            report["status"] = "stopped"
            break
        plan = current()  # Refresh owners, protection tags and seed time before EACH group.
        report["after_gb"] = round(plan["total_bytes"] / GIB, 3)
        report["skipped"] = plan["skipped"]
        if plan["total_bytes"] <= low * GIB:
            report["status"] = "target_reached"
            break
        if not plan["groups"]:
            report["status"] = "no_eligible_group"
            break
        group = plan["groups"][0]
        # Reannounce so tracker accounting is updated before tasks are removed.
        qbc.torrents_reannounce(torrent_hashes=group["hashes"])
        # Last in-memory API refresh narrows the race with concurrent additions/tag changes.
        latest = current()
        matching = next((g for g in latest["groups"] if g["hashes"] == group["hashes"]
                         and g["paths"] == group["paths"]), None)
        if matching is None:
            raise RuntimeError("Ownership or eligibility changed; no deletion issued")
        group = matching
        logger.info(f"保种保护清理：删除整组 {len(group['hashes'])} 个任务，"
                    f"计入刷流 {group['bytes']/GIB:.3f} GB，"
                    f"最短累计做种 {group['seed_seconds']/3600:.2f} 小时，{group['names']}")
        pending = {**group, "status": "requested"}
        report["deleted"].append(pending)
        plugin.save_data("guard_last_run", report)
        qbc.torrents_delete(delete_files=True, torrent_hashes=group["hashes"])
        confirmed = False
        for attempt in range(20):
            time.sleep(0.5)
            present = {t["hash"] for t in qbc.torrents_info()}
            if not present.intersection(group["hashes"]):
                confirmed = True
                break
        if not confirmed:
            pending["status"] = "unconfirmed"
            report["status"] = "verification_failed"
            plugin.save_data("guard_last_run", report)
            raise RuntimeError("Deletion not fully confirmed; further cleanup stopped")
        pending["status"] = "confirmed"
        for task in plugin._task_configs.values():
            if task.downloader != downloader_name:
                continue
            records = plugin._get_task_data(task.id, "torrents") or {}
            changed = False
            for h in group["hashes"]:
                if h in records:
                    records[h].update(deleted=True, deleted_time=time.time(),
                                      delete_reason="全局容量清理，" + "或".join(group["matched_rules"][h]) + "，关联组全部满足条件")
                    changed = True
            if changed:
                plugin._save_task_data(task.id, "torrents", records)
                plugin._recalculate_statistics(task.id)
        plugin.save_data("guard_last_run", report)
    report.setdefault("status", "cycle_complete")
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    plugin.save_data("guard_last_run", report)
    logger.info(f"保种保护清理结束：{report['status']}，剩余 {report.get('after_gb')} GB，"
                f"删除 {sum(len(g['hashes']) for g in report['deleted'])} 个任务")

    return sum(len(g["hashes"]) for g in report["deleted"] if g["status"] == "confirmed")
