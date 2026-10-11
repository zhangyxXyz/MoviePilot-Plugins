"""任务规划和执行流程，预览与执行共用判断且执行前重新读取。"""

import time
import uuid
from dataclasses import asdict, replace
from pathlib import PurePosixPath

from .policy import Decision, Torrent, Observation, build_plan, evaluate, file_deletion_guard, observe
from .download_errors import assess_download_error
from .conditions import assess_conditions


class Engine:
    """依赖注入执行器；测试使用内存存储和下载器替身。"""

    def __init__(self, gateway, read, save, stopped):
        """绑定本轮网关、持久化入口和中断标志。"""
        self.gateway, self.read, self.save, self.stopped = gateway, read, save, stopped
        if hasattr(gateway, "configure_verification"):
            gateway.configure_verification(read, save, stopped)

    def assess(self, task, row, observations: dict, bindings: dict, force=False):
        """复核单个任务，保存连续观察的累计信息并输出可读证据。"""
        torrent = row.torrent
        from .hr_labels import protection_tags
        helper_labels = protection_tags()
        protected = set(task.protect_tags)
        # Only tasks opting into an assistant label follow its current/renamed labels.
        if protected & helper_labels:
            protected |= helper_labels
        task = task.model_copy(update={'hr_local_conditions': False, 'error_protect_unknown_hr': False,
                                       'protect_tags': sorted(protected)})
        torrent = replace(torrent, hr=False)
        import copy
        row = copy.copy(row)
        row.torrent = torrent
        evidence, records = {}, {}
        if (task.mode == "invalid" or task.mode == 'conditions' and task.verify_invalid) and torrent.site_ids and torrent.site_ids.issubset(set(task.site_ids)):
            for site_id in torrent.site_ids:
                key = f"{task.id}:{torrent.downloader}:{torrent.hash}:{site_id}"
                binding = bindings.get(f"{site_id}:{torrent.hash}")
                item = self.gateway.verify(row, site_id, binding, force=force)
                evidence[site_id] = item
                previous = Observation(**observations[key]) if key in observations else None
                current = observe(previous, item, task.observation_gap_hours)
                if current:
                    observations[key] = asdict(current)
                    records[site_id] = current
                else:
                    observations.pop(key, None)
        decision = (assess_download_error(task, row, observations, time.time()) if task.mode == 'download_error'
                    else assess_conditions(task, row, observations, evidence, records, time.time()) if task.mode == 'conditions'
                    else evaluate(task.rule(), torrent, evidence, records, time.time()))
        additional = []
        if task.categories and getattr(row, 'category', '') not in task.categories:
            additional.append('不在选定分类内')
        if task.only_moviepilot and not getattr(row, 'managed', False):
            additional.append('没有 MoviePilot 管理标签')
        if task.max_upload_speed_kib is not None:
            speed = getattr(row, 'upload_speed', None)
            if speed is None or speed > task.max_upload_speed_kib * 1024:
                additional.append('当前上传速度超过上限或未知')
        if torrent.size < task.min_size_gb * 1024**3:
            additional.append("低于体积下限")
        if task.max_size_gb is not None and torrent.size > task.max_size_gb * 1024**3:
            additional.append("超过体积上限")
        if task.path_prefix and not PurePosixPath(row.path).is_relative_to(PurePosixPath(task.path_prefix)):
            additional.append("不在选定目录范围")
        if task.action == "mark" and "清理候选" in torrent.tags:
            additional.append("已经标记为清理候选")
        if additional:
            decision = replace(decision, eligible=False, reasons=decision.reasons + tuple(additional))
        return decision, [asdict(e) for e in evidence.values()]

    def preview(self, task, revision: str, progress=None) -> dict:
        """生成带有效期和配置版本的只读候选计划，执行中不信任客户端名单。"""
        progress_state = {"completed": 0, "total": None, "request_count": 0, "cached_checks": 0, "limited_checks": 0, "skipped_checks": 0}
        def report(**event):
            """合并阶段与计数，将站点请求和缓存复用分别展示。"""
            for field in ["request_count", "cached_checks", "limited_checks", "skipped_checks"]:
                progress_state[field] += event.pop(field + "_delta", 0)
            progress_state.update(event)
            if progress:
                progress(dict(progress_state))
        self.gateway.progress = report
        report(phase="download", message="正在读取下载器任务")
        observations = self.read("observations") or {}
        bindings = self.read("bindings") or {}
        rows = self.gateway.snapshot(task.downloader, manifests=task.action == "deletefile", error_only=task.mode == 'download_error')
        rows = [row for row in rows if task.all_sites or not row.torrent.site_ids or row.torrent.site_ids.intersection(task.site_ids)]
        report(total=len(rows), phase="inventory" if task.action == "deletefile" else "verify",
               message="正在检查跨下载器文件引用" if task.action == "deletefile" else "开始逐项核验")
        if task.mode == "invalid" and hasattr(self.gateway, "verification_priority"):
            rows.sort(key=self.gateway.verification_priority)
        inventory, complete = (self.gateway.inventory() if task.action == "deletefile" else ([], False))
        candidates, details = [], {}
        for index, row in enumerate(rows):
            if self.stopped():
                raise RuntimeError("插件已停止，预览中断")
            # 非目标站点不进入本任务名单，未知归属保留以便定位配置缺失。
            if not task.all_sites and row.torrent.site_ids and not row.torrent.site_ids.intersection(task.site_ids):
                continue
            report(completed=index, phase="verify", message="正在核验种子", current_name=row.torrent.name)
            decision, evidence = self.assess(task, row, observations, bindings)
            if task.action == "deletefile":
                reason = file_deletion_guard(row.torrent, [r.torrent for r in inventory], complete)
                if reason:
                    decision = replace(decision, eligible=False, reasons=decision.reasons + (reason,))
            candidates.append((row.torrent, decision))
            details[row.torrent.hash] = (row, evidence)
            report(completed=index + 1, message="本项核验完成",
                   cached_checks_delta=sum(bool(e.get("cached")) for e in evidence),
                   skipped_checks_delta=sum(bool(e.get('site_skipped')) for e in evidence),
                   limited_checks_delta=sum("限额" in e.get("reason", "") for e in evidence))
        items = []
        for torrent, decision in build_plan(task.rule(), candidates):
            row, evidence = details[torrent.hash]
            items.append({"hash": torrent.hash, "name": torrent.name, "size": torrent.size,
                          "seed_hours": None if torrent.seed_seconds is None else round(torrent.seed_seconds / 3600, 2),
                          "ratio": torrent.ratio, "site_ids": sorted(torrent.site_ids),
                          "eligible": decision.eligible, "reasons": list(decision.reasons),
                          "evidence": evidence, "path": row.path,
                          "client_state": getattr(row, 'client_state', ''), "category": getattr(row, 'category', ''),
                          "error_message": getattr(row, 'error_message', ''),
                          "download_progress": None if getattr(row, 'progress', None) is None else round(row.progress * 100, 2)})
        self.save("observations", observations)
        report(phase="complete", message="核验完成，正在保存预览", current_name="")
        return {"id": uuid.uuid4().hex, "task_id": task.id, "task_name": task.name,
                "revision": revision, "created_at": time.time(), "action": task.action,
                "items": items, "candidate_count": sum(i["eligible"] for i in items),
                "candidate_bytes": sum(i["size"] for i in items if i["eligible"])}

    def execute(self, task, plan: dict, revision: str, progress=None) -> dict:
        """仅处理有效预览中的候选，逐项复核、保存意图并确认结果。"""
        if plan.get("revision") != revision or plan.get("task_id") != task.id:
            raise ValueError("配置已变化，请重新预览")
        if not 0 <= time.time() - plan.get("created_at", 0) <= 900:
            raise ValueError("预览已过期，请重新预览")
        if plan.get("action") != task.action:
            raise ValueError("处理动作已变化，请重新预览")
        if task.action == "preview":
            raise ValueError("该策略仅预览，不执行下载器操作")
        report = {"id": uuid.uuid4().hex, "task_id": task.id, "task_name": task.name,
                  "action": task.action, "started_at": time.time(), "items": [], "status": "running"}
        if progress:
            progress(report)
        receipts = self.read("receipts") or {}
        observations, bindings = self.read("observations") or {}, self.read("bindings") or {}
        handled = 0
        handled_bytes = 0
        for item in plan.get("items", []):
            if not item.get("eligible"):
                continue
            result = {"hash": item["hash"], "name": item["name"], "size": item.get('size', 0),
                      "status": "skipped", "reason": "", "cleanup_reasons": []}
            if self.stopped():
                report["status"] = "stopped"
                break
            identity = f"{task.downloader}:{item['hash']}"
            receipt = receipts.get(identity)
            if receipt and receipt.get("status") in {"requested", "unconfirmed"}:
                result["reason"] = "上次操作结果未确认，请先核对下载器和执行记录"
                report["items"].append(result)
                continue
            try:
                fresh = self.gateway.snapshot(task.downloader, manifests=task.action == "deletefile", error_only=task.mode == 'download_error')
                row = next((r for r in fresh if r.torrent.hash == item["hash"]), None)
                if row is None:
                    result["reason"] = "任务已不存在"
                else:
                    decision, evidence = self.assess(task, row, observations, bindings, force=True)
                    result['size'] = row.torrent.size
                    result['cleanup_reasons'] = list(decision.reasons)
                    if decision.eligible:
                        if task.mode == 'invalid' or task.mode == 'conditions' and task.verify_invalid:
                            result['cleanup_reasons'].append('Tracker 与站点确认失效，连续观察已满足')
                        if task.mode == 'download_error' or task.mode == 'conditions' and task.states:
                            result['cleanup_reasons'].append(f"命中下载器状态：{getattr(row, 'client_state', '')}")
                        if task.mode == 'download_error' or task.mode == 'conditions' and task.observe_state:
                            result['cleanup_reasons'].append('持续异常观察已满足')
                    reason = None
                    if decision.eligible and task.action == "deletefile":
                        inventory, complete = self.gateway.inventory()
                        reason = file_deletion_guard(row.torrent, [r.torrent for r in inventory], complete)
                    if row.path != item.get("path") or row.torrent.size != item.get("size"):
                        reason = "下载目录或任务体积已变化，请重新预览"
                    if not decision.eligible or reason:
                        result["reason"] = reason or "，".join(decision.reasons)
                    elif handled >= task.max_count or handled_bytes + row.torrent.size > task.max_gb * 1024**3:
                        result["reason"] = "达到本轮数量或体积上限"
                    elif task.action in {"mark", "pause"} and self.gateway.confirm(row.torrent, task.action, []):
                        result["reason"] = "下载器已处于目标状态"
                    else:
                        # 先持久化意图，进程中断后不会自动重试不确定的删除。
                        receipts[identity] = {"status": "requested", "action": task.action,
                                              "run_id": report["id"], "task_id": task.id, "time": time.time(),
                                              "downloader": task.downloader,
                                              "hash": row.torrent.hash, "name": row.torrent.name,
                                              "paths": row.local_paths}
                        self.save("receipts", receipts)
                        self.save("observations", observations)
                        handled += 1
                        handled_bytes += row.torrent.size
                        if self.stopped():
                            raise RuntimeError("执行前收到停止信号")
                        accepted = self.gateway.apply(row.torrent, task.action)
                        confirmed = False
                        if accepted:
                            for _ in range(5):
                                if self.gateway.confirm(row.torrent, task.action, row.local_paths):
                                    confirmed = True
                                    break
                                if self.stopped():
                                    break
                                time.sleep(0.4)
                        result.update(status="confirmed" if confirmed else "unconfirmed",
                                      reason="下载器已确认" if confirmed else "操作结果未确认，已阻止自动重试",
                                      evidence=evidence)
                        receipts[identity]["status"] = result["status"]
                        self.save("receipts", receipts)
            except Exception as error:
                result.update(status="failed", reason=f"操作中断（{type(error).__name__}），请检查下载器连接及配置")
                if receipts.get(identity, {}).get("status") == "requested":
                    receipts[identity]["status"] = "unconfirmed"
                    self.save("receipts", receipts)
            report["items"].append(result)
            if progress:
                progress(report)
        self.save("observations", observations)
        report.update(finished_at=time.time(), status="stopped" if self.stopped() else "finished")
        if progress:
            progress(report)
        return report

    def reconcile(self, task_id: str) -> dict:
        """只读核对不确定操作，不补发命令；确认后同步原执行记录。"""
        receipts = self.read("receipts") or {}
        reports = self.read("runs") or []
        confirmed = pending = 0
        for receipt in receipts.values():
            if receipt.get("task_id") != task_id or receipt.get("status") not in {"requested", "unconfirmed"}:
                continue
            if self.stopped():
                break
            torrent = Torrent(receipt["downloader"], receipt["hash"], receipt["name"],
                              frozenset(), False, False, False, None, None, 0)
            try:
                success = self.gateway.confirm(torrent, receipt["action"], receipt.get("paths", []))
            except Exception:
                success = False
            if not success:
                pending += 1
                continue
            receipt["status"] = "confirmed"
            confirmed += 1
            for report in reports:
                if report["id"] == receipt["run_id"]:
                    for item in report["items"]:
                        if item["hash"] == receipt["hash"]:
                            item.update(status="confirmed", reason="后续只读核对已确认目标状态")
        self.save("receipts", receipts)
        self.save("runs", reports)
        return {"confirmed": confirmed, "pending": pending}
