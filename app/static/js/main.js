// ═══════════════════════════════════════════════
//  PORTFOLIO CMS — MAIN JS
// ═══════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {

  window.addEventListener('error', (e) => {
    const t = e.target;
    if (t && t.tagName === 'SCRIPT' && t.src) {
      console.error('[CMS] Failed to load script:', t.src);
    } else if (e && e.message) {
      console.error('[CMS] Runtime error:', e.message);
    }
  }, true);
  window.addEventListener('unhandledrejection', (e) => {
    console.error('[CMS] Unhandled promise rejection:', e.reason);
  });

  setTimeout(() => {
    if (!customElements.get('iconify-icon')) {
      console.error('[CMS] Icon library (Iconify) not registered; icons may not render.');
    }
  }, 0);

  // ── Scroll-reveal animation ──────────────────
  const revealEls = document.querySelectorAll(
    '.proj-card, .skill-item, .testimonial-card, .stat-item, .about-text'
  );
  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry, i) => {
      if (entry.isIntersecting) {
        setTimeout(() => {
          entry.target.style.opacity = '1';
          entry.target.style.transform = 'translateY(0)';
        }, i * 60);
        revealObserver.unobserve(entry.target);
      }
    });
  }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });

  revealEls.forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(20px)';
    el.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
    revealObserver.observe(el);
  });

  // ── Smooth anchor scroll with offset ────────
  document.querySelectorAll('a[href^="#"]').forEach(link => {
    link.addEventListener('click', e => {
      const target = document.querySelector(link.getAttribute('href'));
      if (!target) return;
      e.preventDefault();
      const offset = 80;
      const top = target.getBoundingClientRect().top + window.scrollY - offset;
      window.scrollTo({ top, behavior: 'smooth' });
      // Close mobile menu if open
      document.getElementById('navLinks')?.classList.remove('open');
    });
  });

  // ── Active nav link on scroll ────────────────
  const sections = document.querySelectorAll('section[id]');
  const navLinks = document.querySelectorAll('.nav-links a');
  const scrollSpy = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        navLinks.forEach(link => {
          link.classList.toggle(
            'active',
            link.getAttribute('href') === `#${entry.target.id}`
          );
        });
      }
    });
  }, { threshold: 0.4 });
  sections.forEach(s => scrollSpy.observe(s));

  // ── Typed hero subtitle (optional) ──────────
  const heroSubtitle = document.querySelector('.hero-subtitle');
  if (heroSubtitle && heroSubtitle.dataset.typed) {
    const phrases = heroSubtitle.dataset.typed.split('|');
    let pi = 0, ci = 0, deleting = false;
    const type = () => {
      const phrase = phrases[pi];
      heroSubtitle.textContent = deleting
        ? phrase.slice(0, ci--)
        : phrase.slice(0, ci++);
      if (!deleting && ci === phrase.length + 1) {
        deleting = true;
        setTimeout(type, 1800);
      } else if (deleting && ci === 0) {
        deleting = false;
        pi = (pi + 1) % phrases.length;
        setTimeout(type, 300);
      } else {
        setTimeout(type, deleting ? 40 : 80);
      }
    };
    type();
  }

});
