import {createApp} from 'vue'
import {createVuetify} from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import 'vuetify/styles'
import '@mdi/font/css/materialdesignicons.css'
import Workbench from './Workbench.vue'
const api={get:async p=>{const r=await fetch('/api/v1/'+p,{headers:{Authorization:'Bearer '+sessionStorage.getItem('auto-token')}});return r.json()},post:async(p,b)=>{const r=await fetch('/api/v1/'+p,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+sessionStorage.getItem('auto-token')},body:JSON.stringify(b)});if(!r.ok)throw Error('请求失败');return r.json()}}
createApp(Workbench,{api}).use(createVuetify({components,directives})).mount('#app')
