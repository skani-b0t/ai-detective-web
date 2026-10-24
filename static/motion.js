const revealTargets = document.querySelectorAll('.hero-copy, .hero-fragment, .tabs-hint, .tabs, .panel, .foot');
revealTargets.forEach((el, index) => {
  el.classList.add('reveal');
  el.style.setProperty('--delay', `${Math.min(index, 3) * 80}ms`);
});
const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    entry.target.classList.add('is-visible');
    observer.unobserve(entry.target);
  });
}, { threshold: 0.08 });
revealTargets.forEach(el => observer.observe(el));
(() => {
  const start = document.getElementById('hero-start');
  const input = document.getElementById('input');
  const panel = document.getElementById('case-input');
  const status = document.getElementById('status');
  if (!start || !input || !panel) return;
  const activate = () => {
    panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => input.focus({ preventScroll: true }), 420);
    if (status) status.textContent = '输入要查证的内容，然后点击“开始查证”。';
    start.classList.add('is-used');
    window.setTimeout(() => start.classList.remove('is-used'), 700);
  };
  start.addEventListener('click', activate);
  start.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
  });
})();
