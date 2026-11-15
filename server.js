const http = require('http'), fs = require('fs'), path = require('path');
const root = path.join(__dirname, 'public');
const port = Number(process.env.PORT || 3187);
const backendPort = Number(process.env.BACKEND_PORT || 60372);
const hits = new Map(); let activeAnalyses = 0;
function reject(res, code, message) { res.writeHead(code, {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}); res.end(JSON.stringify({detail:message})); }
http.createServer((req,res)=>{
 const url = new URL(req.url, 'http://localhost');
 if (url.pathname.startsWith('/api/') || url.pathname === '/openapi.json') {
  const remote = req.headers['cf-connecting-ip'];
  if (remote) {
   const now=Date.now(), record=hits.get(remote) || {time:now,count:0};
   if (now-record.time>60000) {record.time=now;record.count=0;} record.count++;hits.set(remote,record);
   if (hits.size>10000) for(const [key,val] of hits) if(now-val.time>60000)hits.delete(key);
   if(record.count>40)return reject(res,429,'Rate limit exceeded. Try again later.');
   if (url.pathname === '/api/analyze') return reject(res,403,'The local file-upload endpoint is not exposed publicly.');
   if (Number(req.headers['content-length']||0)>131072) return reject(res,413,'Request body too large.');
  }
  const isAnalysis = req.method==='POST' && ['/api/v1/forest','/api/v2/cover','/api/v1/analyze'].includes(url.pathname);
  if(isAnalysis && activeAnalyses>=2) return reject(res,429,'Analysis capacity reached. Try again later.');
  if(isAnalysis)activeAnalyses++;
  let released=false;function release(){if(isAnalysis&&!released){released=true;activeAnalyses--}}
  const proxy=http.request({hostname:'127.0.0.1',port:backendPort,path:req.url,method:req.method,headers:req.headers},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res);r.on('end',release)});
  proxy.on('error',()=>{release();if(!res.headersSent)reject(res,502,'Analysis service is unavailable.');else res.end()});
  if(remote){let received=0;req.on('data',chunk=>{received+=chunk.length;if(received>131072){req.unpipe(proxy);if(!res.headersSent)reject(res,413,'Request body too large.');proxy.destroy();release()}})}
  req.on('aborted',()=>{proxy.destroy();release()});res.on('close',release);req.pipe(proxy);return;
 }
 let file=url.pathname;if(file==='/'||file==='/docs')file='/index.html';
 let target;try{target=path.resolve(root,'.'+decodeURIComponent(file))}catch{return reject(res,400,'Invalid path')}
 if(!target.startsWith(root+path.sep))return reject(res,403,'Forbidden');
 fs.readFile(target,(err,data)=>{if(err){res.writeHead(404);return res.end('Not found')}
  const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.mp4':'video/mp4'};
  res.writeHead(200,{'Content-Type':types[path.extname(target)]||'application/octet-stream','Cache-Control':'no-cache'});res.end(data)})
}).listen(port,process.env.HOST||'127.0.0.1',()=>console.log(`http://localhost:${port}`));
