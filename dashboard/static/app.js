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
    storeAll: "Barcha brendlar",
    brandsTitle: "Brendlar",
    brandsHint: "Brend nomini yozing, masalan hm yoki stradivarius. Havola ixtiyoriy: ma'lum brendlar uchun Yevropa sayti o'zi qo'yiladi.",
    btnAddBrand: "Brend qo'shish",
    brandNamePlaceholder: "hm yoki stradivarius",
    brandUrlPlaceholder: "Havola ixtiyoriy",
    brandRemove: "O'chirish",
    brandEmpty: "Hali brend qo'shilmagan.",
    brandErrorEurope: "Yevropa katalogi kerak: Ispaniya, Fransiya, Germaniya, Italiya yoki Buyuk Britaniya. Turkiya va AQSH havolalari qabul qilinmaydi.",
    brandErrorUnknown: "Bu brend ro'yxatda yo'q. O'ngdagi maydonga Yevropa katalogi havolasini qo'ying.",
    brandErrorName: "Brend nomini kiriting.",
    brandAdded: "Brend qo'shildi. Keyingi tsikl shu Yevropa saytidan mahsulot oladi.",
    brandRemoved: "Brend o'chirildi.",
    brandRemoveConfirm: "Bu brendni o'chirasizmi?",
    catAll: "Barcha toifalar",
    catJackets: "Palto va kurtkalar",
    catTrousers: "Shim va jinsilar",
    catDresses: "Ko'ylaklar",
    catKnitwear: "Triko va sviterlar",
    catTops: "Top va ko'ylaklar",
    catSkirts: "Yubkalar",
    catShoes: "Poyabzallar",
    catAccessories: "Sumka va aksessuarlar",
    cat_jackets: "Kurtka / Palto",
    cat_trousers: "Shim / Jinsi",
    cat_dresses: "Ko'ylak",
    cat_knitwear: "Triko",
    cat_tops: "Top / Ko'ylak",
    cat_skirts: "Yubka",
    cat_shoes: "Poyabzal",
    cat_accessories: "Aksessuar",
    cat_other: "Kiyim",

    noProductsTitle: "Ushbu toifada mahsulot topilmadi",
    noProductsSub: "Agregator orqali mahsulotlarni yuklash uchun 'Tsiklni ishga tushirish' tugmasini bosing.",
    viewStore: "Do'konda ko'rish",
    approvePublish: "Tasdiqlash va chop etish",
    deleteProduct: "O'chirish",
    deletePublishedHint: "Chop etilgan mahsulotni o'chirib bo'lmaydi",
    deleteConfirmTitle: "Mahsulotni o'chirish",
    deleteConfirmBody: "Bu mahsulot bazadan o'chiriladi. Bu amalni qaytarib bo'lmaydi.",
    deleteConfirmAction: "O'chirish",
    toastDeleted: "Mahsulot bazadan o'chirildi",
    toastDeletePublished: "Chop etilgan mahsulotni o'chirib bo'lmaydi",
    awaitingGpt: "GPT tahlili va matn tayyorlanishi kutilmoqda...",

    modalPromptTitle: "GPT saralash va kopirayting prompti",
    modalPromptLabel: "Prompt matni (har bir nashr tsiklida fayldan qayta o'qiladi)",
    modalConfigTitle: "Tizim sozlamalari",
    labelMarkup: "Ustama miqdori (Markup)",
    labelCurrency: "Sotish valyutasi (Target Currency)",
    labelMaxItems: "Bir tsiklda saralanadigan mahsulotlar soni",
    labelDailyCap: "Kunlik nashr limiti (bo'sh qoldirilsa - cheklovsiz)",
    labelScheduleTimes: "Nashr vaqtlari",
    labelTimezone: "Vaqt mintaqasi (Timezone)",
    hintScheduleTimes: "Har kuni qaysi vaqtlarda post chop etilishini belgilang. Vaqt qo'shmasangiz – faqat qo'lda ishga tushirish ishlaydi.",
    btnAddTime: "Vaqt qo'shish",
    labelDryRunCheck: "Sinov rejimini yoqish (Telegram/Instagramga yubormasdan tekshirish)",
    labelModerationCheck: "Qo'lda moderatsiyani yoqish (chop etishdan oldin ko'rib chiqish)",
    scheduleManualOnly: "Faqat qo'lda ishga tushirish",
    btnCancel: "Bekor qilish",
    btnSave: "Saqlash",

    status_published: "Chop etildi",
    status_selected: "Tanlandi",
    status_pending_review: "Moderatsiyada",
    status_failed: "Xato",
    status_new: "Yangi",
    status_withdrawn: "Nashrdan olindi",

    toastExecuting: "Nashr tsikli bajarilmoqda...",
    toastSuccess: (pub, unseen) => `Tsikl yakunlandi: ${pub} ta chop etildi, ${unseen} ta yangi mahsulot topildi.`,
    toastPromptSaved: "Prompt saqlandi va darhol kuchga kirdi!",
    toastSettingsSaved: "Sozlamalar saqlandi va yangilandi!",
    toastApproved: (id) => `Mahsulot ${id} tasdiqlandi va chop etildi!`,

    btnLock: "Qulflash",
    modalAuthTitle: "Admin Autentifikatsiyasi",
    modalAuthSubtitle: "Boshqaruv paneliga kirish uchun foydalanuvchi nomi va parolni kiriting",
    labelAdminUsername: "Foydalanuvchi nomi (Username)",
    labelAdminPassword: "Admin Paroli (Password)",
    labelAdminKey: "Admin Kaliti / Parol",
    labelApiUrl: "Backend API Server Manzili (ixtiyoriy)",
    hintApiUrl: "Netlify'da ochilgan bo'lsa, backend API server manzilingizni kiriting.",
    labelRememberKey: "Ushbu brauzerda eslab qolish",
    btnLogin: "Kirish",
    authErrorEmpty: "Iltimos, foydalanuvchi nomi va parolni kiriting.",
    authErrorInvalid: "Foydalanuvchi nomi yoki parol noto'g'ri yoki serverga ulanib bo'lmadi.",
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
    storeAll: "Все бренды",
    brandsTitle: "Бренды",
    brandsHint: "Введите название, например hm или stradivarius. Ссылку можно не заполнять: для известного бренда подставится европейский сайт. Если бренда нет в списке, вставьте ссылку сами.",
    btnAddBrand: "Добавить бренд",
    brandNamePlaceholder: "hm или stradivarius",
    brandUrlPlaceholder: "Ссылка необязательна",
    brandRemove: "Удалить",
    brandEmpty: "Бренды ещё не добавлены.",
    brandErrorEurope: "Нужна ссылка на европейский каталог: Испания, Франция, Германия, Италия или Великобритания. Ссылки на Турцию и США не принимаются.",
    brandErrorUnknown: "Этого бренда нет в списке. Вставьте ссылку на европейский каталог в поле справа.",
    brandErrorName: "Укажите название бренда.",
    brandAdded: "Бренд добавлен. Следующий цикл возьмёт товары с этого европейского сайта.",
    brandRemoved: "Бренд удалён.",
    brandRemoveConfirm: "Удалить этот бренд?",
    catAll: "Все категории",
    catJackets: "Пальто и куртки",
    catTrousers: "Брюки и джинсы",
    catDresses: "Платья",
    catKnitwear: "Трикотаж и свитера",
    catTops: "Топы и рубашки",
    catSkirts: "Юбки",
    catShoes: "Обувь",
    catAccessories: "Сумки и аксессуары",
    cat_jackets: "Куртки / Пальто",
    cat_trousers: "Брюки / Джинсы",
    cat_dresses: "Платья",
    cat_knitwear: "Трикотаж",
    cat_tops: "Топы / Рубашки",
    cat_skirts: "Юбки",
    cat_shoes: "Обувь",
    cat_accessories: "Аксессуары",
    cat_other: "Одежда",

    noProductsTitle: "Товары в этой категории не найдены",
    noProductsSub: "Нажмите 'Запустить цикл', чтобы загрузить товары из агрегатора.",
    viewStore: "В магазин",
    approvePublish: "Одобрить и опубликовать",
    deleteProduct: "Удалить",
    deletePublishedHint: "Опубликованный товар удалить нельзя",
    deleteConfirmTitle: "Удалить товар",
    deleteConfirmBody: "Товар будет удалён из базы. Это действие нельзя отменить.",
    deleteConfirmAction: "Удалить",
    toastDeleted: "Товар удалён из базы",
    toastDeletePublished: "Опубликованный товар удалить нельзя",
    awaitingGpt: "Ожидает отбора GPT и генерации описания...",

    modalPromptTitle: "Промпт GPT для отбора и описания",
    modalPromptLabel: "Текст промпта (перечитывается при каждом запуске цикла)",
    modalConfigTitle: "Операционные настройки",
    labelMarkup: "Размер наценки (Markup)",
    labelCurrency: "Валюта продажи (Target Currency)",
    labelMaxItems: "Максимум товаров за один прогон",
    labelDailyCap: "Дневной лимит публикаций (пусто — без ограничений)",
    labelScheduleTimes: "Время публикаций",
    labelTimezone: "Часовой пояс (Timezone)",
    hintScheduleTimes: "Укажите, в какое время каждый день публиковать посты. Без времен — только вручную.",
    btnAddTime: "Добавить время",
    labelDryRunCheck: "Включить тестовый режим (без отправки в Telegram/Instagram)",
    labelModerationCheck: "Включить ручную модерацию (удерживать посты перед публикацией)",
    scheduleManualOnly: "Только вручную",
    btnCancel: "Отмена",
    btnSave: "Сохранить",

    status_published: "Опубликовано",
    status_selected: "Отобрано",
    status_pending_review: "На модерации",
    status_failed: "Ошибка",
    status_new: "Новый",
    status_withdrawn: "Снято",

    toastExecuting: "Выполняется цикл публикации...",
    toastSuccess: (pub, unseen) => `Цикл завершен: ${pub} опубликовано, ${unseen} новых товаров.`,
    toastPromptSaved: "Промпт успешно сохранен и применен!",
    toastSettingsSaved: "Настройки успешно сохранены и применены!",
    toastApproved: (id) => `Товар ${id} одобрен и опубликован!`,

    btnLock: "Заблокировать",
    modalAuthTitle: "Аутентификация администратора",
    modalAuthSubtitle: "Введите имя пользователя и пароль для доступа к панели управления",
    labelAdminUsername: "Имя пользователя (Логин)",
    labelAdminPassword: "Пароль администратора",
    labelAdminKey: "Ключ администратора / Пароль",
    labelApiUrl: "Адрес сервера API (необязательно)",
    hintApiUrl: "Если панель открыта на Netlify, укажите URL вашего бэкенд сервера.",
    labelRememberKey: "Запомнить в этом браузере",
    btnLogin: "Войти",
    authErrorEmpty: "Пожалуйста, введите имя пользователя и пароль.",
    authErrorInvalid: "Неверное имя пользователя или пароль.",
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
    storeAll: "All Brands",
    brandsTitle: "Brands",
    brandsHint: "Type a brand name, for example hm or stradivarius. The link is optional: a known brand gets its European site automatically. If the brand is not in the list, paste a link.",
    btnAddBrand: "Add brand",
    brandNamePlaceholder: "hm or stradivarius",
    brandUrlPlaceholder: "Link is optional",
    brandRemove: "Remove",
    brandEmpty: "No brands yet.",
    brandErrorEurope: "Use a European catalog link: Spain, France, Germany, Italy, or the UK. Turkey and US links are rejected.",
    brandErrorUnknown: "This brand is not in the list. Paste a European catalog link in the field on the right.",
    brandErrorName: "Enter a brand name.",
    brandAdded: "Brand added. The next cycle will collect products from this European site.",
    brandRemoved: "Brand removed.",
    brandRemoveConfirm: "Remove this brand?",
    catAll: "All Categories",
    catJackets: "Coats & Jackets",
    catTrousers: "Trousers & Jeans",
    catDresses: "Dresses",
    catKnitwear: "Knitwear & Sweaters",
    catTops: "Tops & Shirts",
    catSkirts: "Skirts",
    catShoes: "Shoes",
    catAccessories: "Bags & Accessories",
    cat_jackets: "Coats & Jackets",
    cat_trousers: "Trousers & Jeans",
    cat_dresses: "Dresses",
    cat_knitwear: "Knitwear",
    cat_tops: "Tops & Shirts",
    cat_skirts: "Skirts",
    cat_shoes: "Shoes",
    cat_accessories: "Accessories",
    cat_other: "Clothing",

    noProductsTitle: "No products found in this category",
    noProductsSub: "Click 'Run Cycle Now' to ingest items from the aggregator.",
    viewStore: "View Store",
    approvePublish: "Approve & Publish",
    deleteProduct: "Delete",
    deletePublishedHint: "Published products cannot be deleted",
    deleteConfirmTitle: "Delete product",
    deleteConfirmBody: "This product will be removed from the database. This cannot be undone.",
    deleteConfirmAction: "Delete",
    toastDeleted: "Product removed from the database",
    toastDeletePublished: "Published products cannot be deleted",
    awaitingGpt: "Awaiting GPT curation and copywriting...",

    modalPromptTitle: "Edit GPT Curation & Copy Prompt",
    modalPromptLabel: "Prompt Content (re-read fresh on every publishing cycle)",
    modalConfigTitle: "Operational Settings",
    labelMarkup: "Markup Amount",
    labelCurrency: "Target Sale Currency",
    labelMaxItems: "Max Items Per Run",
    labelDailyCap: "Daily Publish Cap (leave empty for unlimited)",
    labelScheduleTimes: "Publish Schedule Times",
    labelTimezone: "Timezone",
    hintScheduleTimes: "Set the daily times to auto-publish posts. Leave empty to use manual-only mode.",
    btnAddTime: "Add Time",
    labelDryRunCheck: "Enable Dry-Run Mode (Simulate without publishing)",
    labelModerationCheck: "Enable Manual Moderation Gate (hold items for review)",
    scheduleManualOnly: "Manual only",
    btnCancel: "Cancel",
    btnSave: "Save Settings",

    status_published: "Published",
    status_selected: "Selected",
    status_pending_review: "Pending Review",
    status_failed: "Failed",
    status_new: "New",
    status_withdrawn: "Withdrawn",

    toastExecuting: "Executing pipeline cycle...",
    toastSuccess: (pub, unseen) => `Cycle finished: ${pub} published, ${unseen} new items.`,
    toastPromptSaved: "Prompt saved and hot-reloaded successfully!",
    toastSettingsSaved: "Settings saved and hot-reloaded!",
    toastApproved: (id) => `Product ${id} approved and published!`,

    btnLock: "Lock",
    modalAuthTitle: "Admin Authentication",
    modalAuthSubtitle: "Enter admin username and password to unlock the dashboard",
    labelAdminUsername: "Username",
    labelAdminPassword: "Password",
    labelAdminKey: "Admin Security Key / Password",
    labelApiUrl: "Backend API Server URL (optional)",
    hintApiUrl: "If loaded on Netlify, specify your live backend API server URL.",
    labelRememberKey: "Remember on this device",
    btnLogin: "Unlock Dashboard",
    authErrorEmpty: "Please enter your username and password.",
    authErrorInvalid: "Invalid username or password.",
    authSuccess: "Authentication successful!",
    authLoggedOut: "Dashboard locked.",
  }
};

let currentLang = 'ru';
localStorage.setItem('fashion_autopost_lang', 'ru');
let currentFilter = 'all';
let currentStore = 'all';
let currentCategory = 'all';
let latestStats = null;
let _brandStores = null;

function t(key, ...args) {
  const dict = translations.ru;
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
  const brandNameInput = document.getElementById('inputBrandName');
  const brandUrlInput = document.getElementById('inputBrandUrl');
  if (brandNameInput) brandNameInput.placeholder = t('brandNamePlaceholder');
  if (brandUrlInput) brandUrlInput.placeholder = t('brandUrlPlaceholder');
  if (_brandStores) renderBrands(_brandStores);

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

  let scheduleText = '';
  if (data.schedule && data.schedule.times && data.schedule.times.length > 0) {
    scheduleText = `${t('timesPrefix')}${data.schedule.times.join(', ')} (${data.schedule.timezone || 'UTC'})`;
  } else {
    scheduleText = t('scheduleManualOnly');
  }
  document.getElementById('metricScheduleTimes').textContent = scheduleText;

  const modePill = document.getElementById('modePill');
  const modeText = document.getElementById('modeText');
  if (modePill && modeText) {
    if (data.dry_run) {
      modePill.className = 'mode-badge mode-dryrun';
      modeText.textContent = t('modeDryRun');
    } else {
      modePill.className = 'mode-badge mode-live';
      modeText.textContent = t('modeLive');
    }
  }
}

// Storage & API Configuration
const AUTH_KEY_STORAGE = 'fashion_admin_key';
const AUTH_USER_STORAGE = 'fashion_admin_username';
const API_URL_STORAGE = 'fashion_api_url';

function getAuthKey() {
  return localStorage.getItem(AUTH_KEY_STORAGE) || sessionStorage.getItem(AUTH_KEY_STORAGE) || '';
}

function getAuthUser() {
  return localStorage.getItem(AUTH_USER_STORAGE) || sessionStorage.getItem(AUTH_USER_STORAGE) || '';
}

function setAuthKey(key, remember = true) {
  if (remember) {
    localStorage.setItem(AUTH_KEY_STORAGE, key);
  } else {
    sessionStorage.setItem(AUTH_KEY_STORAGE, key);
  }
}

function setAuthUser(user, remember = true) {
  if (remember) {
    localStorage.setItem(AUTH_USER_STORAGE, user);
  } else {
    sessionStorage.setItem(AUTH_USER_STORAGE, user);
  }
}

function clearAuthKey() {
  localStorage.removeItem(AUTH_KEY_STORAGE);
  sessionStorage.removeItem(AUTH_KEY_STORAGE);
  localStorage.removeItem(AUTH_USER_STORAGE);
  sessionStorage.removeItem(AUTH_USER_STORAGE);
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

async function updateCategoryCounts(status, store) {
  try {
    const params = new URLSearchParams();
    if (status !== 'all') params.append('status', status);
    if (store !== 'all') params.append('source', store);
    params.append('limit', '1000');
    const res = await apiFetch(`/api/products?${params.toString()}`);
    const data = await res.json();
    const items = data.products || [];

    const counts = {
      all: items.length,
      jackets: 0,
      trousers: 0,
      dresses: 0,
      knitwear: 0,
      tops: 0,
      skirts: 0,
      shoes: 0,
      accessories: 0,
    };

    items.forEach(it => {
      const cat = it.category || 'other';
      if (counts[cat] !== undefined) {
        counts[cat]++;
      }
    });

    Object.keys(counts).forEach(cat => {
      const el = document.getElementById(`count_${cat}`);
      if (el) el.textContent = counts[cat];
    });
  } catch (e) {
    console.error('Failed to update category counts:', e);
  }
}

async function fetchProducts() {
  const grid = document.getElementById('productsGrid');
  try {
    const params = new URLSearchParams();
    if (currentFilter !== 'all') params.append('status', currentFilter);
    if (currentStore !== 'all') params.append('source', currentStore);
    if (currentCategory !== 'all') params.append('category', currentCategory);
    params.append('limit', '500');

    const res = await apiFetch(`/api/products?${params.toString()}`);
    const data = await res.json();
    const products = data.products || [];

    // Dynamically refresh counts across current status & store
    updateCategoryCounts(currentFilter, currentStore);

    if (products.length === 0) {
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

    grid.innerHTML = products.map(p => {
      const isPending = p.status === 'pending_review';
      const approveBtn = isPending
        ? `<button class="btn btn-primary btn-sm" onclick="approveProduct('${p.external_id}')" style="width: 100%;">
             <i data-lucide="check" style="width: 14px; height: 14px;"></i> ${t('approvePublish')}
           </button>`
        : '';
      const isPublished = p.status === 'published' || p.telegram_post_id || p.instagram_post_id;
      const safeId = escapeHtml(p.external_id);
      const safeTitle = escapeHtml(p.title || '');
      const deleteBtn = isPublished
        ? `<button type="button" class="btn btn-danger btn-sm" disabled title="${escapeHtml(t('deletePublishedHint'))}">
             <i data-lucide="trash-2" style="width: 14px; height: 14px;"></i> ${t('deleteProduct')}
           </button>`
        : `<button type="button" class="btn btn-danger btn-sm" data-delete-product="${safeId}" data-delete-title="${safeTitle}">
             <i data-lucide="trash-2" style="width: 14px; height: 14px;"></i> ${t('deleteProduct')}
           </button>`;

      const priceOrig = `${p.currency_original} ${p.price_original.toFixed(2)}`;
      const priceFinal = p.price_final != null ? `$${Math.floor(Number(p.price_final))}` : '—';
      const desc = p.description_gpt || t('awaitingGpt');
      const statusText = t(`status_${p.status}`) || p.status;
      const catKey = p.category || 'other';
      const catLabel = t(`cat_${catKey}`) || p.category || '';

      return `
        <div class="product-card">
          <div class="product-image-container">
            <span class="brand-overlay">${p.source}</span>
            ${catLabel ? `<span class="category-overlay">${catLabel}</span>` : ''}
            <div class="product-badge-overlay">
              <span class="status-pill status-${p.status}">${statusText}</span>
            </div>
            <img class="product-image" src="${p.photo_url}" alt="${p.title}" loading="lazy" onerror="this.src='https://images.unsplash.com/photo-1490481651871-ab68de25d43d?w=600&auto=format&fit=crop&q=80'">
          </div>
          <div class="product-body">
            <h3 class="product-title">${p.title}</h3>
            <p class="product-desc">${desc}</p>
            ${p.status === 'failed' && p.last_error ? `<p class="product-error">${escapeHtml(p.last_error)}</p>` : ''}
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
            <div class="product-actions">
              ${approveBtn}
              ${deleteBtn}
            </div>
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

let pendingDeleteId = null;

function openDeleteConfirm(externalId, title) {
  pendingDeleteId = externalId;
  const nameEl = document.getElementById('deleteConfirmName');
  if (nameEl) nameEl.textContent = title || externalId;
  document.getElementById('deleteModal').classList.add('active');
  refreshLucide();
}

function closeDeleteConfirm() {
  pendingDeleteId = null;
  const modal = document.getElementById('deleteModal');
  if (modal) modal.classList.remove('active');
}

async function confirmDeleteProduct() {
  const externalId = pendingDeleteId;
  if (!externalId) return;
  const btn = document.getElementById('btnConfirmDelete');
  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch(`/api/products/${encodeURIComponent(externalId)}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      closeDeleteConfirm();
      showToast(t('toastDeleted'));
      fetchStats();
      fetchProducts();
    } else if (res.status === 409) {
      closeDeleteConfirm();
      showToast(t('toastDeletePublished'), true);
    } else {
      showToast(`Error: ${data.detail || res.status}`, true);
    }
  } catch (err) {
    if (err.message !== 'Unauthorized') {
      showToast(`Error: ${err.message}`, true);
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.getElementById('productsGrid').addEventListener('click', (event) => {
  const btn = event.target.closest('[data-delete-product]');
  if (!btn) return;
  openDeleteConfirm(btn.getAttribute('data-delete-product'), btn.getAttribute('data-delete-title'));
});

document.getElementById('btnConfirmDelete').addEventListener('click', confirmDeleteProduct);
document.querySelectorAll('[data-close-delete]').forEach(btn => {
  btn.addEventListener('click', closeDeleteConfirm);
});

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

// Config Modal — time-tag helpers
let _scheduleTimes = [];

function renderTimeTags() {
  const container = document.getElementById('scheduleTimeTags');
  if (!container) return;
  if (_scheduleTimes.length === 0) {
    container.innerHTML = `<span style="color: var(--text-muted, #888); font-size: 12px; align-self: center; padding: 2px 4px;">${t('scheduleManualOnly')}</span>`;
    return;
  }
  container.innerHTML = _scheduleTimes.map((time, idx) => `
    <span style="
      display: inline-flex; align-items: center; gap: 5px;
      background: var(--accent-primary, #7c3aed); color: #fff;
      padding: 3px 10px 3px 12px; border-radius: 9999px; font-size: 13px; font-weight: 600;
    ">
      ${time}
      <button type="button" onclick="removeScheduleTime(${idx})" style="
        background: none; border: none; color: rgba(255,255,255,0.8); cursor: pointer;
        display: flex; align-items: center; padding: 0; margin-left: 2px; font-size: 15px; line-height: 1;
      " title="Remove">&times;</button>
    </span>
  `).join('');
}

window.removeScheduleTime = function(idx) {
  _scheduleTimes.splice(idx, 1);
  renderTimeTags();
};

function addScheduleTime() {
  const input = document.getElementById('inputTimePickerValue');
  if (!input || !input.value) return;
  const val = input.value; // HH:MM from time input
  if (_scheduleTimes.includes(val)) { input.value = ''; return; }
  _scheduleTimes.push(val);
  _scheduleTimes.sort();
  input.value = '';
  renderTimeTags();
}

// Wire Add button
const btnAddTime = document.getElementById('btnAddScheduleTime');
if (btnAddTime) {
  btnAddTime.addEventListener('click', addScheduleTime);
  document.getElementById('inputTimePickerValue')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addScheduleTime(); }
  });
}

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

    // Populate time tags
    _scheduleTimes = (data.schedule && data.schedule.times) ? [...data.schedule.times] : [];
    renderTimeTags();

    // Timezone
    const tzEl = document.getElementById('inputTimezone');
    if (tzEl) tzEl.value = (data.schedule && data.schedule.timezone) ? data.schedule.timezone : 'UTC';

    const checkDryRunEl = document.getElementById('checkDryRun');
    if (checkDryRunEl) checkDryRunEl.checked = !!data.dry_run;
    document.getElementById('checkModeration').checked = !!data.moderation.enabled;

    configModal.classList.add('active');
    refreshLucide();
  } catch (err) {
    console.error('Failed to load config:', err);
  }
});

document.getElementById('btnSaveConfig').addEventListener('click', async () => {
  const dailyCapVal = document.getElementById('inputDailyCap').value;
  const checkDryRunEl = document.getElementById('checkDryRun');
  const tzEl = document.getElementById('inputTimezone');

  const payload = {
    markup: parseFloat(document.getElementById('inputMarkup').value),
    target_currency: document.getElementById('inputCurrency').value.trim().toUpperCase(),
    max_products_per_run: parseInt(document.getElementById('inputMaxItems').value),
    daily_publish_cap: dailyCapVal ? parseInt(dailyCapVal) : null,
    interval_minutes: null,          // always use cron times, never interval
    schedule_times: [..._scheduleTimes],
    timezone: tzEl ? (tzEl.value.trim() || 'UTC') : 'UTC',
    dry_run: checkDryRunEl ? checkDryRunEl.checked : false,
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
    closeDeleteConfirm();
  });
});

// Filter tabs (Status)
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', (e) => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    const target = e.currentTarget || e.target.closest('.tab-btn');
    target.classList.add('active');
    currentFilter = target.dataset.filter;
    fetchProducts();
  });
});

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function brandLabel(name) {
  return String(name).replace(/-/g, ' ');
}

function brandErrorMessage(detail) {
  if (detail === 'non_european_url') return t('brandErrorEurope');
  if (detail === 'unknown_brand') return t('brandErrorUnknown');
  if (detail === 'invalid_brand_name') return t('brandErrorName');
  if (typeof detail === 'string' && detail) return detail;
  return t('brandErrorUnknown');
}

function renderBrands(stores) {
  _brandStores = stores || {};
  const names = Object.keys(_brandStores).sort();
  const list = document.getElementById('brandsList');
  const tabs = document.getElementById('storeFilterTabs');
  if (currentStore !== 'all' && !names.includes(currentStore)) {
    currentStore = 'all';
  }
  if (tabs) {
    tabs.innerHTML = `
      <button type="button" class="store-pill ${currentStore === 'all' ? 'active' : ''}" data-store="all">${t('storeAll')}</button>
      ${names.map((name) => `
        <button type="button" class="store-pill ${currentStore === name ? 'active' : ''}" data-store="${escapeHtml(name)}">${escapeHtml(brandLabel(name))}</button>
      `).join('')}
    `;
  }
  if (!list) return;
  if (names.length === 0) {
    list.innerHTML = `<div class="brands-hint">${t('brandEmpty')}</div>`;
    return;
  }
  list.innerHTML = names.map((name) => {
    const store = _brandStores[name] || {};
    const market = (store.market || '').toUpperCase();
    const currency = store.currency || 'EUR';
    const url = store.url || '';
    return `
      <div class="brand-row">
        <span class="brand-row-name">${escapeHtml(brandLabel(name))}</span>
        <span class="brand-market">${escapeHtml(market || 'EU')} · ${escapeHtml(currency)}</span>
        <a class="brand-row-url" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>
        <button type="button" class="btn btn-secondary btn-sm" data-remove-brand="${escapeHtml(name)}">${t('brandRemove')}</button>
      </div>
    `;
  }).join('');
}

async function loadBrands() {
  try {
    const res = await apiFetch('/api/scraper/stores');
    const data = await res.json();
    renderBrands(data.stores || {});
  } catch (err) {
    if (err.message !== 'Unauthorized') {
      console.error('Failed to load brands:', err);
    }
  }
}

const storeFilterTabs = document.getElementById('storeFilterTabs');
if (storeFilterTabs) {
  storeFilterTabs.addEventListener('click', (e) => {
    const target = e.target.closest('.store-pill');
    if (!target) return;
    document.querySelectorAll('.store-pill').forEach(b => b.classList.remove('active'));
    target.classList.add('active');
    currentStore = target.dataset.store;
    fetchProducts();
  });
}

const brandsList = document.getElementById('brandsList');
if (brandsList) {
  brandsList.addEventListener('click', async (e) => {
    const button = e.target.closest('[data-remove-brand]');
    if (!button) return;
    const name = button.dataset.removeBrand;
    if (!name || !window.confirm(t('brandRemoveConfirm'))) return;
    try {
      const res = await apiFetch(`/api/scraper/stores/${encodeURIComponent(name)}`, { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok) {
        showToast(brandErrorMessage(data.detail), true);
        return;
      }
      showToast(t('brandRemoved'));
      await loadBrands();
      fetchProducts();
    } catch (err) {
      if (err.message !== 'Unauthorized') showToast(err.message, true);
    }
  });
}

const brandAddForm = document.getElementById('brandAddForm');
if (brandAddForm) {
  brandAddForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('inputBrandName').value.trim();
    const url = document.getElementById('inputBrandUrl').value.trim();
    const button = document.getElementById('btnAddBrand');
    if (!name) {
      showToast(t('brandErrorName'), true);
      return;
    }
    if (button) button.disabled = true;
    try {
      const payload = { name, enabled: true, max_items: 60 };
      if (url) payload.url = url;
      const res = await apiFetch('/api/scraper/stores', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(brandErrorMessage(data.detail), true);
        return;
      }
      document.getElementById('inputBrandName').value = '';
      document.getElementById('inputBrandUrl').value = '';
      showToast(t('brandAdded'));
      await loadBrands();
    } catch (err) {
      if (err.message !== 'Unauthorized') showToast(err.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  });
}

// Category filter pills
document.querySelectorAll('.cat-pill').forEach(btn => {
  btn.addEventListener('click', (e) => {
    document.querySelectorAll('.cat-pill').forEach(b => b.classList.remove('active'));
    const target = e.currentTarget || e.target.closest('.cat-pill');
    target.classList.add('active');
    currentCategory = target.dataset.category;
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
const inputAdminUsername = document.getElementById('inputAdminUsername');
const inputAdminPassword = document.getElementById('inputAdminPassword');
const inputAdminKey = document.getElementById('inputAdminKey');
const inputApiUrl = document.getElementById('inputApiUrl');
const btnLogin = document.getElementById('btnLogin');
const btnLock = document.getElementById('btnLock');
const btnTogglePassword = document.getElementById('btnTogglePassword');
const userBadge = document.getElementById('userBadge');
const userNameText = document.getElementById('userNameText');

function updateUserBadge(username) {
  if (userBadge && userNameText) {
    if (username) {
      userNameText.textContent = username;
      userBadge.style.display = 'inline-flex';
    } else {
      userBadge.style.display = 'none';
    }
  }
}

function showAuthModal(errorMsg = null) {
  if (errorMsg) {
    authAlert.textContent = errorMsg;
    authAlert.style.display = 'flex';
  } else {
    authAlert.style.display = 'none';
  }
  if (inputAdminUsername) {
    inputAdminUsername.value = getAuthUser() || 'admin';
  }
  if (inputAdminPassword) {
    inputAdminPassword.value = getAuthKey();
  }
  if (inputAdminKey) {
    inputAdminKey.value = getAuthKey();
  }
  if (inputApiUrl) {
    inputApiUrl.value = getApiBaseUrl();
  }
  authModal.classList.add('active');
  refreshLucide();
}

function hideAuthModal() {
  authModal.classList.remove('active');
  authAlert.style.display = 'none';
}

async function handleLogin() {
  const username = (inputAdminUsername ? inputAdminUsername.value : '').trim() || 'admin';
  const password = (inputAdminPassword ? inputAdminPassword.value : (inputAdminKey ? inputAdminKey.value : '')).trim();
  const apiUrl = inputApiUrl ? inputApiUrl.value.trim() : '';
  const remember = document.getElementById('checkRememberKey').checked;

  if (!password) {
    authAlert.textContent = t('authErrorEmpty');
    authAlert.style.display = 'flex';
    return;
  }

  if (apiUrl) {
    setApiBaseUrl(apiUrl);
  }
  setAuthUser(username, remember);
  setAuthKey(password, remember);

  const loginText = document.getElementById('btnLoginText');
  btnLogin.disabled = true;
  loginText.textContent = '...';

  try {
    const res = await fetch(getFullApiUrl('/api/auth/login'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, key: password })
    });

    if (res.ok) {
      const authData = await res.json();
      const confirmedUser = authData.username || username;
      setAuthUser(confirmedUser, remember);
      updateUserBadge(confirmedUser);
      hideAuthModal();
      showToast(t('authSuccess'));
      await fetchStats();
      await loadBrands();
      await fetchProducts();
    } else {
      clearAuthKey();
      updateUserBadge(null);
      authAlert.textContent = t('authErrorInvalid');
      authAlert.style.display = 'flex';
    }
  } catch (err) {
    try {
      const fallbackRes = await fetch(getFullApiUrl('/api/stats'), {
        headers: { 'Authorization': `Bearer ${password}`, 'X-Admin-Key': password }
      });
      if (fallbackRes.ok) {
        updateUserBadge(username);
        hideAuthModal();
        showToast(t('authSuccess'));
        await loadBrands();
        const statsData = await fallbackRes.json();
        updateStatsUI(statsData);
        await fetchProducts();
      } else {
        clearAuthKey();
        updateUserBadge(null);
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
    updateUserBadge(null);
    showAuthModal();
    showToast(t('authLoggedOut'));
  });
}

if (btnTogglePassword) {
  btnTogglePassword.addEventListener('click', () => {
    const targetInput = inputAdminPassword || inputAdminKey;
    if (!targetInput) return;
    const isPass = targetInput.type === 'password';
    targetInput.type = isPass ? 'text' : 'password';
    const icon = document.getElementById('iconEye');
    if (icon) {
      icon.setAttribute('data-lucide', isPass ? 'eye-off' : 'eye');
      refreshLucide();
    }
  });
}

if (inputAdminUsername) {
  inputAdminUsername.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      if (inputAdminPassword) {
        inputAdminPassword.focus();
      } else {
        handleLogin();
      }
    }
  });
}

if (inputAdminPassword) {
  inputAdminPassword.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleLogin();
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
  updateUserBadge(null);
  showAuthModal();
} else {
  updateUserBadge(getAuthUser() || 'admin');
  fetchStats();
  loadBrands();
  fetchProducts();
}
