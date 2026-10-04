// همگام‌سازی localStorage صفحه با دیتابیس Flask (database/blu.db).
// - لیست اصلی، یوزر و پسورد: در دیتابیس ذخیره می‌شوند.
// - لیست فرعی: هرگز ذخیره نمی‌شود؛ با هر بار باز شدن صفحه از روی لیست اصلی ساخته می‌شود.
// - اگر ذخیره در دیتابیس شکست بخورد، رویداد 'blu:sync-error' فرستاده می‌شود تا صفحه به کاربر نشان بدهد.
(function () {
  const KEYS = {
    primaryCards: 'haj_ashkan_primary_cards',
    secondaryCards: 'haj_ashkan_secondary_cards',
    blueUsername: 'haj_ashkan_blue_username',
    bluePassword: 'haj_ashkan_blue_password',
    nextId: 'haj_ashkan_next_id'
  };

  function reportError(message) {
    console.error('sync failed:', message);
    try {
      window.dispatchEvent(new CustomEvent('blu:sync-error', { detail: message }));
    } catch (e) { /* ignore */ }
  }

  function post(url, body) {
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      keepalive: true
    }).then(function (res) {
      if (res.ok) return;
      return res.json().catch(function () { return {}; }).then(function (j) {
        reportError((j && j.error) || ('HTTP ' + res.status));
      });
    }).catch(function () {
      reportError('ارتباط با سرور برقرار نشد');
    });
  }

  // ---- ۱) بارگذاری از دیتابیس (همزمان، قبل از اجرای کد صفحه) ----
  const realSet = Storage.prototype.setItem;
  try {
    const xhr = new XMLHttpRequest();
    xhr.open('GET', '/api/state', false);
    xhr.send();
    if (xhr.status === 200) {
      const s = JSON.parse(xhr.responseText);
      let primary = s.primary;
      let username = s.username || '';
      let password = s.password || '';

      const dbEmpty = primary.length === 0 && !username;
      if (dbEmpty) {
        // دیتابیس خالی است: اگر لیست اصلی/اطلاعات در مرورگر هست، یک‌بار به دیتابیس منتقل کن
        const lp = localStorage.getItem(KEYS.primaryCards);
        if (lp) { primary = JSON.parse(lp); post('/api/primary', primary); }
        username = localStorage.getItem(KEYS.blueUsername) || '';
        password = localStorage.getItem(KEYS.bluePassword) || '';
        if (username) post('/api/setting', { key: 'username', value: username });
        if (password) post('/api/setting', { key: 'password', value: password });
      }

      // لیست فرعی همیشه کپی تازه‌ای از لیست اصلی است (با آی‌دی‌های جدید)
      let next = Math.max(s.nextId || 1, localStorage.getItem(KEYS.nextId) | 0);
      primary.forEach(function (c) { if (c.id >= next) next = c.id + 1; });
      const secondary = primary.map(function (c) {
        return { id: next++, number: c.number, owner: c.owner };
      });

      realSet.call(localStorage, KEYS.primaryCards, JSON.stringify(primary));
      realSet.call(localStorage, KEYS.secondaryCards, JSON.stringify(secondary));
      realSet.call(localStorage, KEYS.blueUsername, username);
      realSet.call(localStorage, KEYS.bluePassword, password);
      realSet.call(localStorage, KEYS.nextId, String(next));
    }
  } catch (e) {
    console.error('could not load state from server', e);
  }

  // ---- ۲) فقط لیست اصلی و اطلاعات حساب به دیتابیس می‌روند ----
  // لیست اصلی فقط در applySaveAndSync (دکمه ذخیره پروفایل) نوشته می‌شود؛
  // یعنی ویرایش/افزودن/حذف کارت‌های لیست اصلی همان لحظه‌ی «ذخیره» در دیتابیس اعمال می‌شود.
  // لیست فرعی عمداً ارسال نمی‌شود.
  Storage.prototype.setItem = function (key, value) {
    realSet.call(this, key, value);
    if (this !== window.localStorage) return;
    try {
      if (key === KEYS.primaryCards) post('/api/primary', JSON.parse(value));
      else if (key === KEYS.blueUsername) post('/api/setting', { key: 'username', value: value });
      else if (key === KEYS.bluePassword) post('/api/setting', { key: 'password', value: value });
      else if (key === KEYS.nextId) post('/api/setting', { key: 'nextId', value: value });
    } catch (e) {
      reportError('خطا در آماده‌سازی داده برای ذخیره');
    }
  };
})();
