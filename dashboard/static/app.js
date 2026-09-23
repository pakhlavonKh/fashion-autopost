/**
 * Fashion Autopost Dashboard - Multilingual Client Logic with Lucide Icons
 */

const translations = {
  uz: {
    brandSubtitle: "Telegram va Instagram uchun avtomatlashtirilgan kiyimlar nashr markazi",
    modeDryRun: "Sinov rejimi (Dry-Run)",
    modeLive: "Jonli rejim (Live)",
    btnPrompt: "Promptni tahrirlash",
    btnConfig: "Sozlamalar",
    btnRunCycle: "Tsiklni ishga tushirish",
    runningCycle: "Tsikl bajarilmoqda...",

    metricPublishedToday: "Bugun chop etilgan",
    metricPublishedTotal: "Jami chop etilgan",
    metricTotalSub: "Barcha kanallar bo'yicha",
    metricPending: "Moderatsiyada",
    metricPendingSub: "Tasdiqlash kutilmoqda",
    metricMarkup: "Belgilangan ustama",
    noDailyCap: "Kunlik cheklov yo'q",
    dailyCapPrefix: "Kunlik cheklov: ",
    timesPrefix: "Vaqtlar: ",

    catalogTitle: "Mahsulotlar katalogi va moderatsiya",
    tabAll: "Barchasi",
    tabPublished: "Chop etilgan",
    tabPending: "Moderatsiyada",
    tabSelected: "Tanlangan",
    tabNew: "Yangi",
    tabFailed: "Xato",

    noProductsTitle: "Ushbu toifada mahsulot topilmadi",
    noProductsSub: "Agregator orqali mahsulotlarni yuklash uchun 'Tsiklni ishga tushirish' tugmasini bosing.",
    viewStore: "Do'konda ko'rish",
    approvePublish: "Tasdiqlash va chop etish",
    awaitingGpt: "GPT tahlili va matn tayyorlanishi kutilmoqda...",

    modalPromptTitle: "GPT saralash va kopirayting prompti",
    modalPromptLabel: "Prompt matni (har bir nashr tsiklida fayldan qayta o'qiladi)",
    modalConfigTitle: "Tizim sozlamalari",
    labelMarkup: "Ustama miqdori (Markup)",
    labelCurrency: "Sotish valyutasi (Target Currency)",
    labelMaxItems: "Bir tsiklda saralanadigan mahsulotlar soni",
    labelDailyCap: "Kunlik nashr limiti (bo'sh qoldirilsa - cheklovsiz)",
    labelScheduleTimes: "Nashr qilish vaqtlari (vergul bilan ajratilgan HH:MM)",
    labelDryRunCheck: "Sinov rejimini yoqish (Telegram/Instagramga yubormasdan tekshirish)",
    labelModerationCheck: "Qo'lda moderatsiyani yoqish (chop etishdan oldin ko'rib chiqish)",
    btnCancel: "Bekor qilish",
    btnSave: "Saqlash",

    status_published: "Chop etildi",
    status_selected: "Tanlandi",
    status_pending_review: "Moderatsiyada",
    status_failed: "Xato",
    status_new: "Yangi",

    toastExecuting: "Nashr tsikli bajarilmoqda...",
    toastSuccess: (pub, unseen) => `Tsikl yakunlandi: ${pub} ta chop etildi, ${unseen} ta yangi mahsulot topildi.`,
    toastPromptSaved: "Prompt saqlandi va darhol kuchga kirdi!",
    toastSettingsSaved: "Sozlamalar saqlandi va yangilandi!",
    toastApproved: (id) => `Mahsulot ${id} tasdiqlandi va chop etildi!`,

    btnLock: "Qulflash",
    modalAuthTitle: "Admin Autentifikatsiyasi",
    modalAuthSubtitle: "Boshqaruv paneliga kirish uchun xavfsizlik kalitini kiriting",
    labelAdminKey: "Admin Kaliti (API Key / Password)",
    labelApiUrl: "Backend API Server Manzili (ixtiyoriy)",
    hintApiUrl: "Netlify'da ochilgan bo'lsa, backend API server manzilingizni kiriting.",
    labelRememberKey: "Ushbu brauzerda eslab qolish",
    btnLogin: "Kirish",
    authErrorEmpty: "Iltimos, xavfsizlik kalitini kiriting.",
    authErrorInvalid: "Xavfsizlik kaliti noto'g'ri yoki serverga ulanib bo'lmadi.",
    authSuccess: "Muvaffaqiyatli autentifikatsiyadan o'tildi!",
    authLoggedOut: "Boshqaruv paneli qulflandi.",
  },
  ru: {
    brandSubtitle: "Центр управления автопостингом одежды в Telegram и Instagram",
    modeDryRun: "Тестовый режим (Dry-Run)",
    modeLive: "Боевой режим (Live)",
    btnPrompt: "Редактировать промпт",
    btnConfig: "Настройки",
    btnRunCycle: "Запустить цикл",
    runningCycle: "Выполняется цикл...",

    metricPublishedToday: "Опубликовано сегодня",
    metricPublishedTotal: "Всего опубликовано",
    metricTotalSub: "За все время по всем каналам",
    metricPending: "На модерации",
    metricPendingSub: "Ожидают подтверждения",
    metricMarkup: "Текущая наценка",
    noDailyCap: "Дневной лимит не задан",
    dailyCapPrefix: "Лимит в день: ",
    timesPrefix: "Время: ",

    catalogTitle: "Каталог товаров и модерация",
    tabAll: "Все",
    tabPublished: "Опубликовано",
    tabPending: "На модерации",
    tabSelected: "Отобрано",
    tabNew: "Новые",
    tabFailed: "Ошибки",

    noProductsTitle: "Товары в этой категории не найдены",
    noProductsSub: "Нажмите 'Запустить цикл', чтобы загрузить товары из агрегатора.",
    viewStore: "В магазин",
    approvePublish: "Одобрить и опубликовать",
    awaitingGpt: "Ожидает отбора GPT и генерации описания...",

    modalPromptTitle: "Промпт GPT для отбора и описания",
    modalPromptLabel: "Текст промпта (перечитывается при каждом запуске цикла)",
    modalConfigTitle: "Операционные настройки",
    labelMarkup: "Размер наценки (Markup)",
    labelCurrency: "Валюта продажи (Target Currency)",
    labelMaxItems: "Максимум товаров за один прогон",
    labelDailyCap: "Дневной лимит публикаций (пусто — без ограничений)",
    labelScheduleTimes: "Время публикаций (через запятую HH:MM)",
    labelDryRunCheck: "Включить тестовый режим (без отправки в Telegram/Instagram)",
    labelModerationCheck: "Включить ручную модерацию (удерживать посты перед публикацией)",
    btnCancel: "Отмена",
    btnSave: "Сохранить",

    status_published: "Опубликовано",
    status_selected: "Отобрано",
    status_pending_review: "На модерации",
    status_failed: "Ошибка",
    status_new: "Новый",

    toastExecuting: "Выполняется цикл публикации...",
    toastSuccess: (pub, unseen) => `Цикл завершен: ${pub} опубликовано, ${unseen} новых товаров.`,
    toastPromptSaved: "Промпт успешно сохранен и применен!",
    toastSettingsSaved: "Настройки успешно сохранены и применены!",
    toastApproved: (id) => `Товар ${id} одобрен и опубликован!`,

    btnLock: "Заблокировать",
    modalAuthTitle: "Аутентификация администратора",
    modalAuthSubtitle: "Введите ключ безопасности для доступа к панели управления",
    labelAdminKey: "Ключ администратора (API Key / Пароль)",
    labelApiUrl: "Адрес сервера API (необязательно)",
    hintApiUrl: "Если панель открыта на Netlify, укажите URL вашего бэкенд сервера.",
    labelRememberKey: "Запомнить в этом brauzere",
    btnLogin: "Войти",
    authErrorEmpty: "Пожалуйста, введите ключ безопасности.",
    authErrorInvalid: "Неверный ключ безопасности или нет связи с сервером.",
    authSuccess: "Успешная авторизация!",
    authLoggedOut: "Панель управления заблокирована.",
  },
  en: {
    brandSubtitle: "Autonomous Telegram & Instagram Publishing Control Center",
    modeDryRun: "Dry-Run Active",
    modeLive: "Live Mode",
    btnPrompt: "Edit Prompt",
    btnConfig: "Settings",
    btnRunCycle: "Run Cycle Now",
    runningCycle: "Running Cycle...",

    metricPublishedToday: "Published Today",
    metricPublishedTotal: "Total Published",
    metricTotalSub: "All-time across channels",
    metricPending: "Pending Review",
    metricPendingSub: "Moderation queue",
    metricMarkup: "Configured Markup",
    noDailyCap: "No daily cap set",
    dailyCapPrefix: "Cap: ",
    timesPrefix: "Times: ",

    catalogTitle: "Product Catalog & Moderation",
    tabAll: "All",
    tabPublished: "Published",
    tabPending: "Pending Review",
    tabSelected: "Selected",
    tabNew: "New",
    tabFailed: "Failed",

    noProductsTitle: "No products found in this category",
    noProductsSub: "Click 'Run Cycle Now' to ingest items from the aggregator.",
    viewStore: "View Store",
    approvePublish: "Approve & Publish",
    awaitingGpt: "Awaiting GPT curation and copywriting...",

    modalPromptTitle: "Edit GPT Curation & Copy Prompt",
    modalPromptLabel: "Prompt Content (re-read fresh on every publishing cycle)",
    modalConfigTitle: "Operational Settings",
    labelMarkup: "Markup Amount",
    labelCurrency: "Target Sale Currency",
    labelMaxItems: "Max Items Per Run",
    labelDailyCap: "Daily Publish Cap (leave empty for unlimited)",
    labelScheduleTimes: "Schedule Times (comma-separated HH:MM)",
    labelDryRunCheck: "Enable Dry-Run Mode (Simulate without publishing)",
    labelModerationCheck: "Enable Manual Moderation Gate (hold items for review)",
    btnCancel: "Cancel",
    btnSave: "Save Settings",

    status_published: "Published",
    status_selected: "Selected",
    status_pending_review: "Pending Review",
    status_failed: "Failed",
    status_new: "New",

    toastExecuting: "Executing pipeline cycle...",
    toastSuccess: (pub, unseen) => `Cycle finished: ${pub} published, ${unseen} new items.`,
    toastPromptSaved: "Prompt saved and hot-reloaded successfully!",
    toastSettingsSaved: "Settings saved and hot-reloaded!",
    toastApproved: (id) => `Product ${id} approved and published!`,

    btnLock: "Lock",
    modalAuthTitle: "Admin Authentication",
    modalAuthSubtitle: "Enter admin security key to access the control panel",
    labelAdminKey: "Admin Security Key (API Key / Password)",
    labelApiUrl: "Backend API Server URL (optional)",
    hintApiUrl: "If loaded on Netlify, specify your live backend API server URL.",
    labelRememberKey: "Remember on this device",
    btnLogin: "Unlock Dashboard",
    authErrorEmpty: "Please enter your security key.",
    authErrorInvalid: "Invalid security key or server unreachable.",
    authSuccess: "Authentication successful!",
    authLoggedOut: "Dashboard locked.",
  }
};

let currentLang = localStorage.getItem('fashion_autopost_lang') || 'uz';
let currentFilter = 'all';
let latestStats = null;

function t(key, ...args) {
  const dict = translations[currentLang] || translations.uz;
  const val = dict[key];
  if (typeof val === 'function') {
    return val(...args);
  }
  return val || key;
}

function refreshLucide() {
  if (window.lucide && typeof window.lucide.createIcons === 'function') {
    window.lucide.createIcons();
  }
}

function applyTranslations() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.textContent = t(key);
  });

  // Update language buttons active state
  document.querySelectorAll('.lang-btn').forEach(btn => {
    if (btn.dataset.lang === currentLang) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  if (latestStats) {
    updateStatsUI(latestStats);
  }

  refreshLucide();
}

function setLanguage(lang) {
  if (translations[lang]) {
    currentLang = lang;
    localStorage.setItem('fashion_autopost_lang', lang);
    applyTranslations();
    fetchProducts();
  }
}

function showToast(message, isError = false) {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.style.borderColor = isError ? 'var(--danger)' : 'var(--accent-primary)';
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 4000);
}

function updateStatsUI(data) {
  latestStats = data;
  document.getElementById('metricPublishedToday').textContent = data.published_today;
  document.getElementById('metricPublishedTotal').textContent = data.published_total;
  document.getElementById('metricPending').textContent = data.pending_review;

  const capText = data.daily_publish_cap
    ? `${t('dailyCapPrefix')}${data.daily_publish_cap}`
    : t('noDailyCap');
  document.getElementById('metricPublishedCap').textContent = capText;

  const symbol = data.target_currency === 'USD' ? '$' : `${data.target_currency} `;
  document.getElementById('metricMarkup').textContent = `${symbol}${data.markup.toFixed(2)}`;
  document.getElementById('metricScheduleTimes').textContent = `${t('timesPrefix')}${data.schedule.times.join(', ')} (${data.schedule.timezone})`;

  const modePill = document.getElementById('modePill');
  const modeText = document.getElementById('modeText');
  if (data.dry_run) {
    modePill.className = 'mode-badge mode-dryrun';
    modeText.textContent = t('modeDryRun');
  } else {
    modePill.className = 'mode-badge mode-live';
    modeText.textContent = t('modeLive');
  }
}

// Storage & API Configuration
const AUTH_KEY_STORAGE = 'fashion_admin_key';
const API_URL_STORAGE = 'fashion_api_url';

function getAuthKey() {
  return localStorage.getItem(AUTH_KEY_STORAGE) || sessionStorage.getItem(AUTH_KEY_STORAGE) || '';
}

function setAuthKey(key, remember = true) {
  if (remember) {
    localStorage.setItem(AUTH_KEY_STORAGE, key);
  } else {
    sessionStorage.setItem(AUTH_KEY_STORAGE, key);
  }
}

function clearAuthKey() {
  localStorage.removeItem(AUTH_KEY_STORAGE);
  sessionStorage.removeItem(AUTH_KEY_STORAGE);
}

function getApiBaseUrl() {
  return localStorage.getItem(API_URL_STORAGE) || '';
}

function setApiBaseUrl(url) {
  if (url) {
    localStorage.setItem(API_URL_STORAGE, url.trim().replace(/\/$/, ''));
  } else {
    localStorage.removeItem(API_URL_STORAGE);
  }
}

function getFullApiUrl(endpoint) {
  const base = getApiBaseUrl();
  if (!base) return endpoint;
  return `${base}${endpoint}`;
}

async function apiFetch(endpoint, options = {}) {
  const url = getFullApiUrl(endpoint);
  const token = getAuthKey();
  const headers = { ...(options.headers || {}) };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
    headers['X-Admin-Key'] = token;
  }
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    showAuthModal(t('authErrorInvalid'));
    throw new Error('Unauthorized');
  }
  return res;
}

async function fetchStats() {
  try {
    const res = await apiFetch('/api/stats');
    const data = await res.json();
    updateStatsUI(data);
    refreshLucide();
  } catch (err) {
    console.error('Failed to fetch stats:', err);
  }
}

async function fetchProducts() {
  const grid = document.getElementById('productsGrid');
  try {
    const url = currentFilter === 'all' ? '/api/products' : `/api/products?status=${currentFilter}`;
    const res = await apiFetch(url);
    const data = await res.json();

    if (!data.products || data.products.length === 0) {
      grid.innerHTML = `
        <div style="grid-column: 1 / -1; text-align: center; padding: 64px 20px; color: var(--text-muted); background: var(--bg-card); border-radius: var(--radius-md); border: 1px dashed var(--border-subtle);">
          <div style="display: flex; justify-content: center; margin-bottom: 12px;">
            <i data-lucide="package-open" style="width: 48px; height: 48px; color: var(--text-muted); stroke-width: 1.5;"></i>
          </div>
          <div style="font-size: 16px; font-weight: 500; color: var(--text-secondary);">${t('noProductsTitle')}</div>
          <div style="font-size: 13px; margin-top: 4px;">${t('noProductsSub')}</div>
        </div>
      `;
      refreshLucide();
      return;
    }

    grid.innerHTML = data.products.map(p => {
      const isPending = p.status === 'pending_review';
      const approveBtn = isPending
        ? `<button class="btn btn-primary btn-sm" onclick="approveProduct('${p.external_id}')" style="margin-top: 12px; width: 100%;">
             <i data-lucide="check" style="width: 14px; height: 14px;"></i> ${t('approvePublish')}
           </button>`
        : '';

      const priceOrig = `${p.currency_original} ${p.price_original.toFixed(2)}`;
      const priceFinal = p.price_final ? `$${p.price_final.toFixed(2)}` : '—';
      const desc = p.description_gpt || t('awaitingGpt');
      const statusText = t(`status_${p.status}`) || p.status;

      return `
        <div class="product-card">
          <div class="product-image-container">
            <span class="brand-overlay">${p.source}</span>
            <div class="product-badge-overlay">
              <span class="status-pill status-${p.status}">${statusText}</span>
            </div>
            <img class="product-image" src="${p.photo_url}" alt="${p.title}" loading="lazy" onerror="this.src='https://images.unsplash.com/photo-1490481651871-ab68de25d43d?w=600&auto=format&fit=crop&q=80'">
          </div>
          <div class="product-body">
            <h3 class="product-title">${p.title}</h3>
            <p class="product-desc">${desc}</p>
            <div class="price-row">
              <div>
                <span class="price-original">${priceOrig}</span>
              </div>
              <span class="price-final">${priceFinal}</span>
            </div>
            <div class="product-meta">
              <span>ID: ${p.external_id}</span>
              ${p.product_url ? `<a href="${p.product_url}" target="_blank" style="color: var(--accent-primary); text-decoration: none; display: inline-flex; align-items: center; gap: 4px;">
                ${t('viewStore')} <i data-lucide="external-link" style="width: 12px; height: 12px;"></i>
              </a>` : ''}
            </div>
            ${approveBtn}
          </div>
        </div>
      `;
    }).join('');

    refreshLucide();
  } catch (err) {
    if (err.message !== 'Unauthorized') {
      grid.innerHTML = `<div style="grid-column: 1 / -1; color: var(--danger); text-align: center;">Error: ${err.message}</div>`;
    }
  }
}

async function approveProduct(externalId) {
  try {
    const res = await apiFetch(`/api/products/${externalId}/approve`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast(t('toastApproved', externalId));
      fetchStats();
      fetchProducts();
    } else {
      showToast(`Error: ${data.detail}`, true);
    }
  } catch (err) {
    if (err.message !== 'Unauthorized') {
      showToast(`Error: ${err.message}`, true);
    }
  }
}

// Trigger Cycle
document.getElementById('btnRunCycle').addEventListener('click', async () => {
  const btn = document.getElementById('btnRunCycle');
  const btnText = document.getElementById('btnRunCycleText');
  const btnIcon = document.getElementById('btnRunCycleIcon');
  btn.disabled = true;
  btnText.textContent = t('runningCycle');
  btnIcon.setAttribute('data-lucide', 'loader-2');
  btnIcon.classList.add('spin');
  refreshLucide();

  try {
    showToast(t('toastExecuting'));
    const res = await apiFetch('/api/run-cycle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    const data = await res.json();

    if (res.ok) {
      const s = data.summary;
      showToast(t('toastSuccess', s.published, s.unseen));
      await fetchStats();
      await fetchProducts();
    } else {
      showToast(`Error: ${data.detail}`, true);
    }
  } catch (err) {
    if (err.message !== 'Unauthorized') {
      showToast(`Network error: ${err.message}`, true);
    }
  } finally {
    btn.disabled = false;
    btnText.textContent = t('btnRunCycle');
    btnIcon.setAttribute('data-lucide', 'zap');
    btnIcon.classList.remove('spin');
    refreshLucide();
  }
});

// Prompt Modal
const promptModal = document.getElementById('promptModal');
document.getElementById('btnOpenPrompt').addEventListener('click', async () => {
  try {
    const res = await apiFetch('/api/config');
    const data = await res.json();
    document.getElementById('promptTextarea').value = data.prompt || '';
    promptModal.classList.add('active');
    refreshLucide();
  } catch (err) {
    console.error('Failed to load prompt config:', err);
  }
});

document.getElementById('btnSavePrompt').addEventListener('click', async () => {
  const prompt = document.getElementById('promptTextarea').value;
  try {
    const res = await apiFetch('/api/prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt })
    });
    if (res.ok) {
      showToast(t('toastPromptSaved'));
      promptModal.classList.remove('active');
    } else {
      showToast('Error saving prompt', true);
    }
  } catch (err) {
    if (err.message !== 'Unauthorized') {
      showToast('Error saving prompt: ' + err.message, true);
    }
  }
});

// Config Modal
const configModal = document.getElementById('configModal');
document.getElementById('btnOpenConfig').addEventListener('click', async () => {
  try {
    const res = await apiFetch('/api/config');
    const data = await res.json();

    document.getElementById('inputMarkup').value = data.markup;
    document.getElementById('inputCurrency').value = data.target_currency;
    document.getElementById('inputMaxItems').value = data.max_products_per_run;
    document.getElementById('inputDailyCap').value = data.daily_publish_cap || '';
    document.getElementById('inputScheduleTimes').value = data.schedule.times.join(', ');
    document.getElementById('checkDryRun').checked = !!data.dry_run;
    document.getElementById('checkModeration').checked = !!data.moderation.enabled;

    configModal.classList.add('active');
    refreshLucide();
  } catch (err) {
    console.error('Failed to load config:', err);
  }
});

document.getElementById('btnSaveConfig').addEventListener('click', async () => {
  const timesStr = document.getElementById('inputScheduleTimes').value;
  const times = timesStr.split(',').map(s => s.trim()).filter(Boolean);
  const dailyCapVal = document.getElementById('inputDailyCap').value;

  const payload = {
    markup: parseFloat(document.getElementById('inputMarkup').value),
    target_currency: document.getElementById('inputCurrency').value.trim().toUpperCase(),
    max_products_per_run: parseInt(document.getElementById('inputMaxItems').value),
    daily_publish_cap: dailyCapVal ? parseInt(dailyCapVal) : null,
    schedule_times: times,
    dry_run: document.getElementById('checkDryRun').checked,
    moderation_enabled: document.getElementById('checkModeration').checked,
  };

  try {
    const res = await apiFetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      showToast(t('toastSettingsSaved'));
      configModal.classList.remove('active');
      fetchStats();
    } else {
      showToast('Error saving settings', true);
    }
  } catch (err) {
    if (err.message !== 'Unauthorized') {
      showToast('Error saving settings: ' + err.message, true);
    }
  }
});

// Close modals
document.querySelectorAll('.close-modal').forEach(btn => {
  btn.addEventListener('click', () => {
    promptModal.classList.remove('active');
    configModal.classList.remove('active');
  });
});

// Filter tabs
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', (e) => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    currentFilter = e.target.dataset.filter;
    fetchProducts();
  });
});

// Language switcher clicks
document.querySelectorAll('.lang-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    setLanguage(btn.dataset.lang);
  });
});

// Auth Modal & Security Gate Logic
const authModal = document.getElementById('authModal');
const authAlert = document.getElementById('authAlert');
const inputAdminKey = document.getElementById('inputAdminKey');
const inputApiUrl = document.getElementById('inputApiUrl');
const btnLogin = document.getElementById('btnLogin');
const btnLock = document.getElementById('btnLock');
const btnTogglePassword = document.getElementById('btnTogglePassword');

function showAuthModal(errorMsg = null) {
  if (errorMsg) {
    authAlert.textContent = errorMsg;
    authAlert.style.display = 'flex';
  } else {
    authAlert.style.display = 'none';
  }
  inputAdminKey.value = getAuthKey();
  inputApiUrl.value = getApiBaseUrl();
  authModal.classList.add('active');
  refreshLucide();
}

function hideAuthModal() {
  authModal.classList.remove('active');
  authAlert.style.display = 'none';
}

async function handleLogin() {
  const key = inputAdminKey.value.trim();
  const apiUrl = inputApiUrl.value.trim();
  const remember = document.getElementById('checkRememberKey').checked;

  if (!key) {
    authAlert.textContent = t('authErrorEmpty');
    authAlert.style.display = 'flex';
    return;
  }

  setApiBaseUrl(apiUrl);
  setAuthKey(key, remember);

  const loginText = document.getElementById('btnLoginText');
  btnLogin.disabled = true;
  loginText.textContent = '...';

  try {
    const res = await fetch(getFullApiUrl('/api/auth/login'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key })
    });

    if (res.ok) {
      hideAuthModal();
      showToast(t('authSuccess'));
      await fetchStats();
      await fetchProducts();
    } else {
      clearAuthKey();
      authAlert.textContent = t('authErrorInvalid');
      authAlert.style.display = 'flex';
    }
  } catch (err) {
    try {
      const fallbackRes = await fetch(getFullApiUrl('/api/stats'), {
        headers: { 'Authorization': `Bearer ${key}`, 'X-Admin-Key': key }
      });
      if (fallbackRes.ok) {
        hideAuthModal();
        showToast(t('authSuccess'));
        const statsData = await fallbackRes.json();
        updateStatsUI(statsData);
        await fetchProducts();
      } else {
        clearAuthKey();
        authAlert.textContent = t('authErrorInvalid');
        authAlert.style.display = 'flex';
      }
    } catch (fallbackErr) {
      authAlert.textContent = `${t('authErrorInvalid')} (${err.message})`;
      authAlert.style.display = 'flex';
    }
  } finally {
    btnLogin.disabled = false;
    loginText.textContent = t('btnLogin');
    refreshLucide();
  }
}

if (btnLogin) {
  btnLogin.addEventListener('click', handleLogin);
}

if (btnLock) {
  btnLock.addEventListener('click', () => {
    clearAuthKey();
    showAuthModal();
    showToast(t('authLoggedOut'));
  });
}

if (btnTogglePassword) {
  btnTogglePassword.addEventListener('click', () => {
    const isPass = inputAdminKey.type === 'password';
    inputAdminKey.type = isPass ? 'text' : 'password';
    const icon = document.getElementById('iconEye');
    if (icon) {
      icon.setAttribute('data-lucide', isPass ? 'eye-off' : 'eye');
      refreshLucide();
    }
  });
}

if (inputAdminKey) {
  inputAdminKey.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleLogin();
  });
}

// Initial boot
applyTranslations();
if (!getAuthKey()) {
  showAuthModal();
} else {
  fetchStats();
  fetchProducts();
}
