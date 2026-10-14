const form=document.getElementById('login'),message=document.getElementById('message');
form.onsubmit=async e=>{
  e.preventDefault();const button=form.querySelector('button');button.disabled=true;message.textContent='正在登录…';
  try{
    const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'登录失败');
    form.elements.password.value='';location.replace('/');
  }catch(e){message.textContent=e.message;}finally{button.disabled=false;}
};
