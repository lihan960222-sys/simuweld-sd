const buttons=document.querySelectorAll('nav button');
for(const button of buttons) button.addEventListener('click',()=>{
  for(const item of buttons)item.setAttribute('aria-pressed',String(item===button));
  const field=document.querySelector('#field');
  field.src=button.dataset.img;
  field.alt=button.textContent;
  document.querySelector('#original').href=button.dataset.img;
  document.querySelector('#caption').textContent=button.dataset.text;
});
