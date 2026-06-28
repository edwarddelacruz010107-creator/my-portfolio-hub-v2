/**
 * static/js/themes/scroll-reveal.js
 * Lightweight IntersectionObserver-based scroll reveal.
 * Adds .in-view to observed elements when they enter the viewport.
 * Used by both Default Clean and Developer Pro themes.
 * Respects prefers-reduced-motion (CSS handles disabling the effect).
 */
(function () {
  'use strict';

  const SELECTORS = [
    '.dp-section',
    '.section-services',
    '.section-testimonials',
    '.section-certificates',
    '.section-badges',
    '.dc-section',
  ];

  function init() {
    const targets = document.querySelectorAll(SELECTORS.join(','));
    if (!targets.length || !('IntersectionObserver' in window)) {
      // Fallback: show everything immediately
      targets.forEach(el => el.classList.add('in-view'));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            entry.target.classList.add('in-view');
            observer.unobserve(entry.target); // fire once
          }
        });
      },
      { threshold: 0.08, rootMargin: '0px 0px -40px 0px' }
    );

    targets.forEach(el => observer.observe(el));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
