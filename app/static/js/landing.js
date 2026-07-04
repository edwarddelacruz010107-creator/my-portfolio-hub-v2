/* =============================================================
   MyPortfolioHub — main.js
   Vanilla JS only. No frameworks, no build step.
   ============================================================= */
(function () {
  'use strict';

  /* -----------------------------------------------------------
     1. Navbar: scroll state + mobile menu toggle
     ----------------------------------------------------------- */
  const navbar = document.getElementById('navbar');
  const navToggle = document.getElementById('navToggle');
  const mobileMenu = document.getElementById('mobileMenu');

  function onScrollNav() {
    if (window.scrollY > 12) {
      navbar.classList.add('is-scrolled');
    } else {
      navbar.classList.remove('is-scrolled');
    }
  }
  window.addEventListener('scroll', onScrollNav, { passive: true });
  onScrollNav();

  if (navToggle && mobileMenu) {
    navToggle.addEventListener('click', () => {
      const isOpen = mobileMenu.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', String(isOpen));
      navToggle.innerHTML = isOpen
        ? '<i class="bi bi-x-lg" aria-hidden="true"></i>'
        : '<i class="bi bi-list" aria-hidden="true"></i>';
    });

    // Close mobile menu after choosing a link
    mobileMenu.querySelectorAll('a').forEach((link) => {
      link.addEventListener('click', () => {
        mobileMenu.classList.remove('open');
        navToggle.setAttribute('aria-expanded', 'false');
        navToggle.innerHTML = '<i class="bi bi-list" aria-hidden="true"></i>';
      });
    });
  }

  /* -----------------------------------------------------------
     2. Reveal-on-scroll via IntersectionObserver
     ----------------------------------------------------------- */
  const revealTargets = document.querySelectorAll('[data-reveal]');
  if ('IntersectionObserver' in window && revealTargets.length) {
    const revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('in-view');
            revealObserver.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15, rootMargin: '0px 0px -40px 0px' }
    );
    revealTargets.forEach((el) => revealObserver.observe(el));
  } else {
    revealTargets.forEach((el) => el.classList.add('in-view'));
  }

  /* -----------------------------------------------------------
     3. Animated stat counters
     ----------------------------------------------------------- */
  const counters = document.querySelectorAll('[data-count]');
  function animateCounter(el) {
    const target = parseInt(el.getAttribute('data-count'), 10) || 0;
    const suffix = el.getAttribute('data-suffix') || '';
    const duration = 1400;
    const start = performance.now();

    function tick(now) {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // ease-out-cubic
      const value = Math.floor(eased * target);
      el.textContent = value.toLocaleString('en-US') + suffix;
      if (progress < 1) requestAnimationFrame(tick);
      else el.textContent = target.toLocaleString('en-US') + suffix;
    }
    requestAnimationFrame(tick);
  }

  if ('IntersectionObserver' in window && counters.length) {
    const counterObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            animateCounter(entry.target);
            counterObserver.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.5 }
    );
    counters.forEach((el) => counterObserver.observe(el));
  } else {
    counters.forEach(animateCounter);
  }

  /* -----------------------------------------------------------
     4. Testimonial carousel
     ----------------------------------------------------------- */
  const track = document.getElementById('testimonialSlides');
  const dotsWrap = document.getElementById('testimonialDots');
  const prevBtn = document.getElementById('tPrev');
  const nextBtn = document.getElementById('tNext');

  if (track) {
    const slides = Array.from(track.children);
    let current = 0;
    let autoTimer;

    slides.forEach((_, i) => {
      const dot = document.createElement('button');
      dot.className = 't-dot' + (i === 0 ? ' active' : '');
      dot.setAttribute('aria-label', 'Go to testimonial ' + (i + 1));
      dot.addEventListener('click', () => goTo(i));
      dotsWrap.appendChild(dot);
    });

    function goTo(index) {
      current = (index + slides.length) % slides.length;
      track.style.transform = `translateX(-${current * 100}%)`;
      dotsWrap.querySelectorAll('.t-dot').forEach((d, i) => {
        d.classList.toggle('active', i === current);
      });
      restartAuto();
    }

    function restartAuto() {
      clearInterval(autoTimer);
      autoTimer = setInterval(() => goTo(current + 1), 6000);
    }

    if (nextBtn) nextBtn.addEventListener('click', () => goTo(current + 1));
    if (prevBtn) prevBtn.addEventListener('click', () => goTo(current - 1));

    restartAuto();
  }

  /* -----------------------------------------------------------
     5. FAQ accordion
     ----------------------------------------------------------- */
  const faqItems = document.querySelectorAll('.faq-item');
  faqItems.forEach((item) => {
    const btn = item.querySelector('.faq-q');
    const answer = item.querySelector('.faq-a');
    btn.addEventListener('click', () => {
      const isOpen = item.classList.contains('open');

      faqItems.forEach((other) => {
        other.classList.remove('open');
        other.querySelector('.faq-q').setAttribute('aria-expanded', 'false');
        other.querySelector('.faq-a').style.maxHeight = null;
      });

      if (!isOpen) {
        item.classList.add('open');
        btn.setAttribute('aria-expanded', 'true');
        answer.style.maxHeight = answer.scrollHeight + 'px';
      }
    });
  });

  /* -----------------------------------------------------------
     6. Button ripple effect
     ----------------------------------------------------------- */
  document.querySelectorAll('.ripple-wrap').forEach((btn) => {
    btn.addEventListener('click', function (e) {
      const rect = btn.getBoundingClientRect();
      const ripple = document.createElement('span');
      const size = Math.max(rect.width, rect.height);
      ripple.className = 'ripple';
      ripple.style.width = ripple.style.height = size + 'px';
      ripple.style.left = (e.clientX - rect.left - size / 2) + 'px';
      ripple.style.top = (e.clientY - rect.top - size / 2) + 'px';
      btn.appendChild(ripple);
      setTimeout(() => ripple.remove(), 650);
    });
  });

  /* -----------------------------------------------------------
     6b. Like buttons (projects)
         Live AJAX reactions for signed-in users.
     ----------------------------------------------------------- */
  function getPublicCsrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
  }

  function formatLikeButton(btn, liked, count) {
    btn.dataset.liked = liked ? '1' : '0';
    btn.setAttribute('aria-pressed', String(liked));
    btn.setAttribute('aria-label', liked ? 'Unlike this project' : 'Like this project');
    btn.classList.toggle('liked', liked);
    const labelEl = btn.querySelector('.like-label');
    if (labelEl) {
      labelEl.textContent = liked ? 'Liked' : 'Like';
    }
    const countEl = btn.querySelector('.like-count');
    if (countEl) {
      countEl.textContent = count;
    }
  }

  function setLikeButtonLoading(btn, loading) {
    btn.disabled = loading;
    btn.classList.toggle('loading', loading);
  }

  function showToast(message, type = 'error') {
    let toast = document.getElementById('ph-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'ph-toast';
      toast.style.position = 'fixed';
      toast.style.bottom = '20px';
      toast.style.left = '50%';
      toast.style.transform = 'translateX(-50%)';
      toast.style.padding = '14px 18px';
      toast.style.background = type === 'error' ? 'rgba(220, 38, 38, 0.95)' : 'rgba(22, 163, 74, 0.95)';
      toast.style.color = '#fff';
      toast.style.borderRadius = '999px';
      toast.style.boxShadow = '0 14px 40px rgba(0,0,0,0.25)';
      toast.style.zIndex = '9999';
      toast.style.fontSize = '0.95rem';
      toast.style.maxWidth = '90%';
      toast.style.textAlign = 'center';
      toast.style.opacity = '0';
      toast.style.transition = 'opacity 180ms ease, transform 180ms ease';
      document.body.appendChild(toast);
    }

    toast.textContent = message;
    toast.style.opacity = '1';
    toast.style.transform = 'translateX(-50%) translateY(0)';

    clearTimeout(showToast.timeout);
    showToast.timeout = setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(-50%) translateY(8px)';
    }, 3200);
  }

  async function toggleProjectLike(btn) {
    const projectId = btn.dataset.projectId;
    if (!projectId) {
      return;
    }

    if (btn.classList.contains('signin-required')) {
      const currentUrl = window.location.pathname + window.location.search + window.location.hash;
      window.location.href = '/auth?tab=signin&next=' + encodeURIComponent(currentUrl);
      return;
    }

    const liked = btn.dataset.liked === '1';
    const countEl = btn.querySelector('.like-count');
    const currentCount = parseInt(countEl.textContent, 10) || 0;
    const targetCount = liked ? Math.max(currentCount - 1, 0) : currentCount + 1;

    formatLikeButton(btn, !liked, targetCount);
    btn.classList.remove('pop');
    void btn.offsetWidth;
    btn.classList.add('pop');

    setLikeButtonLoading(btn, true);
    const url = liked
      ? `/api/projects/${projectId}/unlike`
      : `/api/projects/${projectId}/like`;

    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: {
          'Accept': 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': getPublicCsrfToken(),
        },
        credentials: 'same-origin',
      });
      const json = await resp.json().catch(() => ({}));
      if (!resp.ok || json.success !== true) {
        throw new Error(json.message || 'Unable to update reaction.');
      }
      formatLikeButton(btn, json.liked === true, json.like_count ?? targetCount);
    } catch (error) {
      formatLikeButton(btn, liked, currentCount);
      showToast(error?.message || 'Unable to update like. Please try again.');
    } finally {
      setLikeButtonLoading(btn, false);
    }
  }

  document.querySelectorAll('.proj-card .like-btn').forEach((btn) => {
    btn.addEventListener('click', () => toggleProjectLike(btn));
  });

  /* -----------------------------------------------------------
     7. Back-to-top button
     ----------------------------------------------------------- */
  const toTop = document.getElementById('toTop');
  if (toTop) {
    window.addEventListener(
      'scroll',
      () => {
        toTop.classList.toggle('show', window.scrollY > 600);
      },
      { passive: true }
    );
    toTop.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }

  /* -----------------------------------------------------------
     8. Contact form — submit to backend endpoint
     ----------------------------------------------------------- */
  const contactForm = document.getElementById('contactForm');
  if (contactForm) {
    contactForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const submitBtn = contactForm.querySelector('button[type="submit"]');
      const originalHTML = submitBtn.innerHTML;
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span class="loading">Sending…</span>';

      // Gather form data
      const formData = new FormData(contactForm);
      if (!formData.has('name') && formData.has('full_name')) {
        formData.set('name', formData.get('full_name'));
      }
      const url = contactForm.dataset.fallbackUrl || contactForm.action || '/contact/submit';

      try {
        const resp = await fetch(url, {
          method: 'POST',
          headers: { 'Accept': 'application/json' },
          body: formData,
          credentials: 'same-origin',
        });

        const json = await resp.json().catch(() => ({}));
        if (resp.ok) {
          submitBtn.innerHTML = '<i class="bi bi-check2" aria-hidden="true"></i> Message sent';
          contactForm.reset();
          setTimeout(() => {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHTML;
          }, 2500);
        } else {
          submitBtn.disabled = false;
          submitBtn.innerHTML = originalHTML;
          alert(json.error || json.message || 'Failed to send message. Please try again.');
        }
      } catch (err) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalHTML;
        alert('Network error. Please try again later.');
      }
    });
  }

  /* -----------------------------------------------------------
     9. Subtle hero parallax on mouse move (desktop only)
     ----------------------------------------------------------- */
  const heroVisual = document.querySelector('.hero-visual');
  const browserMock = document.querySelector('.browser-mock');
  if (heroVisual && browserMock && window.matchMedia('(min-width: 992px)').matches) {
    heroVisual.addEventListener('mousemove', (e) => {
      const rect = heroVisual.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width - 0.5;
      const y = (e.clientY - rect.top) / rect.height - 0.5;
      browserMock.style.transform =
        `perspective(1400px) rotateY(${-6 + x * 6}deg) rotateX(${2 - y * 6}deg)`;
    });
    heroVisual.addEventListener('mouseleave', () => {
      browserMock.style.transform = '';
    });
  }

  /* -----------------------------------------------------------
     10. Set current year in footer
     ----------------------------------------------------------- */
  const yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* -----------------------------------------------------------
     11. Smooth-scroll offset correction for in-page anchors
         (native CSS scroll-behavior handles the smoothness;
          this only guards against JS-disabled edge cases)
     ----------------------------------------------------------- */
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener('click', function (e) {
      const id = this.getAttribute('href');
      if (id.length > 1) {
        const target = document.querySelector(id);
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          history.pushState(null, '', id);
        }
      }
    });
  });
})();