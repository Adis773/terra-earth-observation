(()=>{
  const key='terra-storage-notice-v1';
  try{if(localStorage.getItem(key)==='acknowledged')return}catch{}
  const style=document.createElement('style');style.textContent=`.terra-notice{position:fixed;z-index:10000;right:20px;bottom:20px;width:min(410px,calc(100vw - 40px));padding:22px;background:#fff;color:#141b16;border:1px solid #dbe1da;box-shadow:0 16px 48px #0c151522;font-family:Manrope,Arial,sans-serif;border-radius:8px}.terra-notice strong{display:block;font-size:16px;letter-spacing:-.02em;margin-bottom:8px}.terra-notice p{font-size:12px;line-height:1.65;color:#5d6960;margin:0 0 16px}.terra-notice-actions{display:flex;align-items:center;justify-content:space-between;gap:16px}.terra-notice a{font-size:12px;color:#364b3b;text-decoration:underline}.terra-notice button{font:700 12px Manrope,Arial,sans-serif;background:#172019;color:white;border:0;border-radius:4px;padding:11px 19px;cursor:pointer}.terra-notice button:focus-visible,.terra-notice a:focus-visible{outline:2px solid #4a8260;outline-offset:3px}`;document.head.append(style);
  const box=document.createElement('aside');box.className='terra-notice';box.setAttribute('role','region');box.setAttribute('aria-label','Storage notice');
  box.innerHTML='<strong></strong><p></p><div class="terra-notice-actions"><a href="/data.html"></a><button type="button"></button></div>';
  const text={en:['Storage and data','Terra saves your language and this notice on your device. Maps, fonts and libraries load from external providers.','Learn more','Accept'],ru:['Данные и хранение','Terra сохраняет язык и отметку об этом уведомлении на вашем устройстве. Карты, шрифты и библиотеки загружаются у внешних поставщиков.','Подробнее','Принять']};
  function update(){let lang='en';try{if(localStorage.getItem('terra-language')==='ru')lang='ru'}catch{}let t=text[lang];box.querySelector('strong').textContent=t[0];box.querySelector('p').textContent=t[1];box.querySelector('a').textContent=t[2];box.querySelector('button').textContent=t[3]}
  box.querySelector('button').addEventListener('click',()=>{try{localStorage.setItem(key,'acknowledged')}catch{}box.remove()});
  for(const id of ['language','landing-language'])document.getElementById(id)?.addEventListener('change',()=>setTimeout(update,0));
  document.body.append(box);update();
})();
