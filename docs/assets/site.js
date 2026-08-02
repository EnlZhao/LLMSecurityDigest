(() => {
  const root = document.documentElement;
  const themeButton = document.querySelector('.theme-toggle');
  const storedTheme = localStorage.getItem('digest-theme');
  root.dataset.theme = storedTheme || 'light';

  themeButton?.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    localStorage.setItem('digest-theme', next);
  });

  const navToggle = document.querySelector('.nav-toggle');
  const nav = document.querySelector('.primary-nav');
  navToggle?.addEventListener('click', () => {
    const open = nav?.classList.toggle('is-open');
    navToggle.setAttribute('aria-expanded', String(Boolean(open)));
  });

  const progress = document.querySelector('.reading-progress span');
  const updateProgress = () => {
    if (!progress) return;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    progress.style.width = `${max > 0 ? Math.min(100, window.scrollY / max * 100) : 0}%`;
  };
  document.addEventListener('scroll', updateProgress, { passive: true });
  updateProgress();

  const paperSearch = document.querySelector('#paper-search');
  const paperCards = [...document.querySelectorAll('[data-paper-search]')];
  const visibleCount = document.querySelector('#visible-count');
  paperSearch?.addEventListener('input', () => {
    const query = paperSearch.value.trim().toLowerCase();
    let visible = 0;
    paperCards.forEach(card => {
      const match = !query || card.dataset.paperSearch.includes(query);
      card.classList.toggle('is-hidden', !match);
      if (match) visible += 1;
    });
    document.querySelectorAll('[data-category-section]').forEach(section => {
      section.classList.toggle('is-empty', !section.querySelector('.paper-card:not(.is-hidden)'));
    });
    if (visibleCount) visibleCount.textContent = String(visible);
  });

  document.querySelectorAll('.paper-action[href^="#paper-"]').forEach(link => {
    link.addEventListener('click', event => {
      event.preventDefault();
      const url = `${location.origin}${location.pathname}${link.getAttribute('href')}`;
      navigator.clipboard?.writeText(url);
      const original = link.firstChild?.textContent;
      if (link.firstChild) link.firstChild.textContent = '已复制 ';
      setTimeout(() => { if (link.firstChild) link.firstChild.textContent = original; }, 1200);
      history.replaceState(null, '', link.getAttribute('href'));
    });
  });

  const figureTriggers = document.querySelectorAll('[data-figure-src]');
  if (figureTriggers.length) {
    const viewer = document.createElement('div');
    viewer.className = 'figure-viewer';
    viewer.setAttribute('role', 'dialog');
    viewer.setAttribute('aria-modal', 'true');
    viewer.setAttribute('aria-label', '论文系统图');
    viewer.innerHTML = '<div class="figure-viewer-frame"><button class="figure-viewer-close" type="button" aria-label="关闭大图">×</button><img alt=""></div>';
    document.body.append(viewer);
    const viewerImage = viewer.querySelector('img');
    const closeViewer = () => {
      viewer.classList.remove('is-open');
      document.body.style.overflow = '';
      viewerImage.removeAttribute('src');
    };

    figureTriggers.forEach(trigger => {
      trigger.addEventListener('click', () => {
        viewerImage.alt = trigger.dataset.figureAlt || '论文系统图';
        viewerImage.src = trigger.dataset.figureSrc;
        viewer.classList.add('is-open');
        document.body.style.overflow = 'hidden';
        viewer.querySelector('.figure-viewer-close').focus();
      });
    });
    viewer.querySelector('.figure-viewer-close').addEventListener('click', closeViewer);
    viewer.addEventListener('click', event => { if (event.target === viewer) closeViewer(); });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && viewer.classList.contains('is-open')) closeViewer();
    });
  }

  const archiveSearch = document.querySelector('#archive-search');
  archiveSearch?.addEventListener('input', () => {
    const query = archiveSearch.value.trim();
    document.querySelectorAll('[data-archive-date]').forEach(row => {
      row.classList.toggle('is-hidden', !row.dataset.archiveDate.includes(query));
    });
  });
})();
