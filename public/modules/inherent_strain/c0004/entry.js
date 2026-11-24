// Keep the model workspace open when inspecting the saved calculation.
window.addEventListener('load', () => {
  const toolbar = document.querySelector('header');
  if (!toolbar) return;
  const link = document.createElement('a');
  link.href = '/modules/inherent_strain/c0004/index.html';
  link.target = '_blank';
  link.rel = 'noopener';
  link.textContent = '双板算例 ↗';
  link.style.cssText = 'white-space:nowrap;color:#16645f;font:600 13px system-ui;text-decoration:underline;text-underline-offset:4px;margin:0 6px';
  toolbar.insertBefore(link,toolbar.querySelector('#export'));
});
