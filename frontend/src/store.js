import {api} from './api.js';
export const store={user:null,probes:[],sweeps:[],layouts:[],certificates:[],runs:[],mounts:[],runMounts:[],batches:[],deliveries:[],sourceVersions:[],layoutId:null,probeId:1};
export async function refresh(){[store.probes,store.sweeps,store.layouts,store.certificates,store.runs,store.mounts,store.runMounts,store.batches,store.deliveries,store.sourceVersions]=await Promise.all([api('/probes'),api('/sweeps'),api('/layouts'),api('/calibration-certificates'),api('/tunnel-runs'),api('/model-mounts'),api('/run-mounts'),api('/acquisition-batches'),api('/tunnel-deliveries'),api('/source-versions')]);if(!store.layoutId&&store.layouts.length)store.layoutId=store.layouts[0].id;return store}
export const layout=s=>s.layouts.find(x=>x.id===Number(s.layoutId));
export const versionSeq=(s,id)=>s.sourceVersions.find(v=>v.id===id)?.seq??'—';
