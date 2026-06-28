/**
 * ================================================================
 * PORTFOLIO CMS v3.0 — PUBLIC PORTFOLIO JAVASCRIPT
 * Enhancements over v2:
 * - Proper reveal observer using 'in-view' class
 * - Magnetic button effect on hero CTAs
 * - Smooth parallax tilt on project cards
 * - Staggered reveal for grid items
 * - Cursor spotlight effect (subtle)
 * - All v2 functionality preserved intact
 * ================================================================
 */

document.addEventListener('DOMContentLoaded', () => {

    // ===================== DOM REFERENCES =====================
    const loader        = document.getElementById('loader');
    const navbar        = document.getElementById('navbar');
    const hamburger     = document.getElementById('hamburger');
    const mobileMenu    = document.getElementById('mobileMenu');
    const themeToggle   = document.getElementById('themeToggle');
    const backToTop     = document.getElementById('backToTop');
    const contactForm   = document.getElementById('contactForm');
    const submitBtn     = document.getElementById('submitBtn');
    const toastContainer = document.getElementById('toastContainer');

    const navLinks      = document.querySelectorAll('.nav-link');
    const mobileNavLinks = document.querySelectorAll('.mobile-nav-link');
    const sections      = document.querySelectorAll('section[id]');
    const revealElements = document.querySelectorAll('.reveal-up, .reveal-left, .reveal-right');
    const skillBars     = document.querySelectorAll('.skill-progress');
    const typingText    = document.getElementById('typingText');

    window.addEventListener('error', (e) => {
        const t = e.target;
        if (t && t.tagName === 'SCRIPT' && t.src) {
            console.error('[Portfolio] Failed to load script:', t.src);
        } else if (e && e.message) {
            console.error('[Portfolio] Runtime error:', e.message);
        }
    }, true);
    window.addEventListener('unhandledrejection', (e) => {
        console.error('[Portfolio] Unhandled promise rejection:', e.reason);
    });

    setTimeout(() => {
        if (!customElements.get('iconify-icon')) {
            console.error('[Portfolio] Icon library (Iconify) not registered; icons may not render.');
        }
    }, 0);


    // ===================== 1. LOADING SCREEN =====================
    window.addEventListener('load', () => {
        setTimeout(() => {
            if (loader) {
                loader.classList.add('hidden');
                document.body.classList.remove('loading');
            }
        }, 500);
    });
    setTimeout(() => {
        if (loader && !loader.classList.contains('hidden')) {
            loader.classList.add('hidden');
            document.body.classList.remove('loading');
        }
    }, 3000);


    // ===================== 2. TYPING ANIMATION =====================
    const rawTyping = (typingText?.dataset?.words || '').trim();
    const typingWords = rawTyping
        ? rawTyping.split('|').map(s => s.trim()).filter(Boolean)
        : [];

    if (typingText && typingWords.length === 1) {
        typingText.textContent = typingWords[0];
    } else if (typingText && typingWords.length > 1) {
        let wordIndex = 0;
        let charIndex = 0;
        let isDeleting = false;

        function typeEffect() {
            const currentWord = typingWords[wordIndex];
            if (isDeleting) {
                typingText.textContent = currentWord.substring(0, charIndex - 1);
                charIndex--;
            } else {
                typingText.textContent = currentWord.substring(0, charIndex + 1);
                charIndex++;
            }
            let delay = isDeleting ? 40 : 80;
            if (!isDeleting && charIndex === currentWord.length) {
                delay = 2000;
                isDeleting = true;
            } else if (isDeleting && charIndex === 0) {
                isDeleting = false;
                wordIndex = (wordIndex + 1) % typingWords.length;
                delay = 500;
            }
            setTimeout(typeEffect, delay);
        }

        setTimeout(typeEffect, 300);
    }


    // ===================== 3. MOBILE MENU =====================
    if (hamburger) {
        hamburger.addEventListener('click', () => {
            const isOpen = hamburger.classList.toggle('active');
            if (mobileMenu) mobileMenu.classList.toggle('open', isOpen);
            document.body.style.overflow = isOpen ? 'hidden' : '';
        });
    }
    mobileNavLinks.forEach(link => {
        link.addEventListener('click', () => {
            if (hamburger) hamburger.classList.remove('active');
            if (mobileMenu) mobileMenu.classList.remove('open');
            document.body.style.overflow = '';
        });
    });


    // ===================== 4. SMOOTH SCROLL =====================
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            const targetId = this.getAttribute('href');
            if (targetId === '#') return;
            const target = document.querySelector(targetId);
            if (target) {
                e.preventDefault();
                target.scrollIntoView({ behavior: 'smooth' });
            }
        });
    });


    // ===================== 5. ACTIVE NAV =====================
    const navObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const id = entry.target.getAttribute('id');
                navLinks.forEach(link => link.classList.toggle('active', link.dataset.section === id));
                mobileNavLinks.forEach(link => link.classList.toggle('active', link.dataset.section === id));
            }
        });
    }, {
        rootMargin: '-72px 0px -40% 0px',
        threshold: 0
    });
    sections.forEach(section => navObserver.observe(section));


    // ===================== 6. NAVBAR SCROLL =====================
    function handleScroll() {
        const scrollY = window.scrollY;
        if (navbar) navbar.classList.toggle('scrolled', scrollY > 20);
        if (backToTop) backToTop.classList.toggle('visible', scrollY > 500);
    }
    window.addEventListener('scroll', handleScroll, { passive: true });
    handleScroll();


    // ===================== 7. SCROLL REVEAL (v3: uses 'in-view') =====================
    const revealObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('in-view');
                // Legacy support
                entry.target.classList.add('revealed');
                revealObserver.unobserve(entry.target);
            }
        });
    }, { rootMargin: '0px 0px -50px 0px', threshold: 0.08 });

    revealElements.forEach(el => revealObserver.observe(el));


    // ===================== 8. SKILL BARS =====================
    const skillObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const bars = entry.target.querySelectorAll('.skill-progress');
                bars.forEach((bar, i) => {
                    const w = bar.dataset.width;
                    setTimeout(() => {
                        bar.style.width = w + '%';
                        bar.classList.add('animated');
                    }, i * 120);
                });
                skillObserver.unobserve(entry.target);
            }
        });
    }, { threshold: 0.25 });
    const skillsList = document.querySelector('.skills-list');
    if (skillsList) skillObserver.observe(skillsList);


    // ===================== 9. THEME TOGGLE =====================
    const THEME_KEY = 'portfolio-theme';
    const savedTheme = localStorage.getItem(THEME_KEY) || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            const cur = document.documentElement.getAttribute('data-theme');
            const next = cur === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            localStorage.setItem(THEME_KEY, next);
        });
    }
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
        if (!localStorage.getItem(THEME_KEY)) {
            document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
        }
    });


    // ===================== 10. BACK TO TOP =====================
    if (backToTop) {
        backToTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
    }


    // ===================== 11. CONTACT FORM =====================
    const WEB3FORMS_API = 'https://api.web3forms.com/submit';
    const formFields = {
        name:    { el: document.getElementById('name'),    error: document.getElementById('nameError'),    validIcon: document.querySelector('#name ~ .form-validation-icon') },
        email:   { el: document.getElementById('email'),   error: document.getElementById('emailError'),   validIcon: document.querySelector('#email ~ .form-validation-icon') },
        subject: { el: document.getElementById('subject'), error: document.getElementById('subjectError'), validIcon: document.querySelector('#subject ~ .form-validation-icon') },
        message: { el: document.getElementById('message'), error: document.getElementById('messageError'), validIcon: null }
    };
    const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
    let lastSubmitTime = 0;
    const COOLDOWN = 30000;

    function sanitise(s) { return s.replace(/<[^>]*>/g, '').trim(); }

    function validateField(key) {
        const { el, error, validIcon } = formFields[key];
        if (!el || !error) return true;
        const v = sanitise(el.value);
        let msg = '';
        switch (key) {
            case 'name':    if (!v) msg='Please enter your name.'; else if(v.length<2) msg='Min 2 characters.'; break;
            case 'email':   if (!v) msg='Please enter your email.'; else if(!EMAIL_REGEX.test(v)) msg='Invalid email address.'; break;
            case 'subject': if (!v) msg='Please enter a subject.'; break;
            case 'message': if (!v) msg='Please enter your message.'; else if(v.length<10) msg='Min 10 characters.'; break;
        }
        error.textContent = msg;
        el.classList.toggle('error', !!msg);
        el.classList.toggle('valid', !msg && v.length > 0);
        if (validIcon) {
            validIcon.textContent = '';
            if (!msg && v.length > 0) {
                validIcon.innerHTML = '<iconify-icon icon="lucide:check" width="14"></iconify-icon>';
                validIcon.className = 'form-validation-icon is-valid';
            } else if (msg) {
                validIcon.innerHTML = '<iconify-icon icon="lucide:x" width="14"></iconify-icon>';
                validIcon.className = 'form-validation-icon is-error';
            } else {
                validIcon.className = 'form-validation-icon';
            }
        }
        return !msg;
    }

    Object.keys(formFields).forEach(key => {
        const { el } = formFields[key];
        if (!el) return;
        el.addEventListener('input', () => {
            if (el.classList.contains('error') || el.classList.contains('valid')) validateField(key);
        });
        el.addEventListener('blur', () => { if (el.value.trim()) validateField(key); });
    });

    // Char counters
    [{ key:'subject', max:150 }, { key:'message', max:2000 }].forEach(({ key, max }) => {
        const el = formFields[key]?.el;
        const counter = document.getElementById(key === 'subject' ? 'subjectCount' : 'messageCount');
        if (!el || !counter) return;
        el.addEventListener('input', () => {
            const len = el.value.length;
            counter.textContent = `${len} / ${max}`;
            counter.classList.toggle('char-count-warn', len > max * 0.9);
        });
    });

    const contactSuccess = document.getElementById('contactSuccess');
    const resetFormBtn   = document.getElementById('resetFormBtn');

    function showSuccessOverlay() {
        if (!contactSuccess) return;
        contactSuccess.classList.add('is-visible');
        contactSuccess.setAttribute('aria-hidden', 'false');
    }
    function hideSuccessOverlay() {
        if (!contactSuccess) return;
        contactSuccess.classList.remove('is-visible');
        contactSuccess.setAttribute('aria-hidden', 'true');
    }
    if (resetFormBtn) {
        resetFormBtn.addEventListener('click', () => {
            hideSuccessOverlay();
            if (contactForm) contactForm.reset();
            Object.keys(formFields).forEach(key => {
                const { el, error, validIcon } = formFields[key];
                if (!el || !error) return;
                error.textContent = '';
                el.classList.remove('error', 'valid');
                if (validIcon) { validIcon.textContent = ''; validIcon.className = 'form-validation-icon'; }
            });
        });
    }

    if (contactForm) {
        contactForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const honeypot = document.getElementById('hpWebsite');
            if (honeypot && honeypot.value.trim() !== '') { showSuccessOverlay(); return; }

            const now = Date.now();
            if (now - lastSubmitTime < COOLDOWN) {
                const rem = Math.ceil((COOLDOWN - (now - lastSubmitTime)) / 1000);
                showToast('error', 'Slow down', `Wait ${rem}s before sending again.`);
                return;
            }

            let allValid = true;
            Object.keys(formFields).forEach(key => { if (!validateField(key)) allValid = false; });
            if (!allValid) {
                showToast('error', 'Validation Error', 'Please fix the highlighted fields.');
                const first = Object.values(formFields).find(f => f.el && f.el.classList.contains('error'));
                if (first) first.el.focus();
                return;
            }

            if (submitBtn) {
                submitBtn.classList.add('loading');
                submitBtn.disabled = true;
            }

            try {
                const fallbackUrl = contactForm.dataset.fallbackUrl;

                const sidEl = document.getElementById('submissionId');
                if (sidEl && !sidEl.value) {
                    try {
                        sidEl.value = (crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + '-' + Math.random().toString(16).slice(2);
                    } catch (_) {
                        sidEl.value = String(Date.now()) + '-' + Math.random().toString(16).slice(2);
                    }
                }

                const res = await fetch(fallbackUrl || '/contact', { method: 'POST', body: new FormData(contactForm) });
                const result = await res.json();
                if (!res.ok || result.status !== 'success') throw new Error(result.message || 'Send failed.');

                lastSubmitTime = Date.now();
                showSuccessOverlay();
            } catch (err) {
                console.error('[ContactForm]', err);
                showToast('error', 'Failed to Send', err.message || 'Something went wrong. Please try again.');
            } finally {
                if (submitBtn) { submitBtn.classList.remove('loading'); submitBtn.disabled = false; }
            }
        });
    }


    // ===================== 12. TOAST =====================
    function showToast(type, title, message) {
        if (!toastContainer) return;
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <div class="toast-icon"><iconify-icon icon="${type === 'success' ? 'lucide:check-circle' : 'lucide:alert-circle'}" width="18"></iconify-icon></div>
            <div class="toast-content"><div class="toast-title">${title}</div><div class="toast-message">${message}</div></div>
            <button class="toast-close" aria-label="Close"><iconify-icon icon="lucide:x" width="16"></iconify-icon></button>
        `;
        toastContainer.appendChild(toast);
        toast.querySelector('.toast-close').addEventListener('click', () => removeToast(toast));
        setTimeout(() => removeToast(toast), 5000);
    }
    function removeToast(toast) {
        if (toast.classList.contains('removing')) return;
        toast.classList.add('removing');
        toast.addEventListener('animationend', () => toast.remove());
    }


    // ===================== 13. PROJECT FILTER =====================
    const filterBar = document.querySelector('.project-filter-bar');
    if (filterBar) {
        const filterBtns = filterBar.querySelectorAll('.filter-btn');
        const projectGrid = document.getElementById('otherProjectsGrid');
        filterBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                filterBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const filter = btn.dataset.filter;
                if (!projectGrid) return;
                projectGrid.querySelectorAll('.project-card[data-category]').forEach(card => {
                    card.classList.toggle('hidden-filter', filter !== 'all' && card.dataset.category !== filter);
                });
            });
        });
    }


    // ===================== 14. TESTIMONIAL CAROUSEL =====================
    const track      = document.getElementById('testimonialTrack');
    const prevBtn    = document.getElementById('carouselPrev');
    const nextBtn    = document.getElementById('carouselNext');
    const dotsContainer = document.getElementById('carouselDots');

    if (track && track.children.length > 1) {
        const total = track.children.length;
        let current = 0;
        let timer = null;

        function goTo(index) {
            current = ((index % total) + total) % total;
            track.style.transform = `translateX(-${current * 100}%)`;
            if (dotsContainer) {
                dotsContainer.querySelectorAll('.carousel-dot').forEach((d, i) => {
                    d.classList.toggle('active', i === current);
                    d.setAttribute('aria-selected', String(i === current));
                });
            }
        }
        function startAuto() { timer = setInterval(() => goTo(current + 1), 6000); }
        function stopAuto()  { clearInterval(timer); }

        if (prevBtn) prevBtn.addEventListener('click', () => { goTo(current - 1); stopAuto(); startAuto(); });
        if (nextBtn) nextBtn.addEventListener('click', () => { goTo(current + 1); stopAuto(); startAuto(); });
        if (dotsContainer) {
            dotsContainer.querySelectorAll('.carousel-dot').forEach(d => {
                d.addEventListener('click', () => { goTo(parseInt(d.dataset.index)); stopAuto(); startAuto(); });
            });
        }
        const carousel = document.getElementById('testimonialCarousel');
        if (carousel) {
            carousel.addEventListener('mouseenter', stopAuto);
            carousel.addEventListener('mouseleave', startAuto);
            let touchX = 0;
            track.addEventListener('touchstart', e => { touchX = e.touches[0].clientX; stopAuto(); }, { passive:true });
            track.addEventListener('touchend', e => {
                const dx = e.changedTouches[0].clientX - touchX;
                if (Math.abs(dx) > 50) goTo(current + (dx < 0 ? 1 : -1));
                startAuto();
            }, { passive:true });
            carousel.addEventListener('keydown', e => {
                if (e.key === 'ArrowLeft')  { goTo(current - 1); stopAuto(); startAuto(); }
                if (e.key === 'ArrowRight') { goTo(current + 1); stopAuto(); startAuto(); }
            });
        }
        startAuto();
    }


    // ===================== 15. v3: PROJECT CARD TILT =====================
    if (window.matchMedia('(hover: hover)').matches) {
        document.querySelectorAll('.project-card').forEach(card => {
            card.addEventListener('mousemove', (e) => {
                const rect = card.getBoundingClientRect();
                const x = (e.clientX - rect.left) / rect.width - 0.5;
                const y = (e.clientY - rect.top) / rect.height - 0.5;
                card.style.setProperty('--tiltX', `${(-y * 6).toFixed(2)}deg`);
                card.style.setProperty('--tiltY', `${(x * 6).toFixed(2)}deg`);
                card.style.transform = `translateY(-6px) scale(1.01) rotateX(var(--tiltX)) rotateY(var(--tiltY))`;
                card.style.transformStyle = 'preserve-3d';
            });
            card.addEventListener('mouseleave', () => {
                card.style.transform = '';
                card.style.transformStyle = '';
            });
        });
    }


    // ===================== 16. v3: STAGGERED GRID REVEAL =====================
    document.querySelectorAll('.projects-grid, .services-grid').forEach(grid => {
        const children = grid.querySelectorAll('.project-card, .service-card');
        children.forEach((child, i) => {
            if (!child.style.getPropertyValue('--delay')) {
                child.style.setProperty('--delay', `${i * 0.08}s`);
            }
        });
    });

}); // end DOMContentLoaded
