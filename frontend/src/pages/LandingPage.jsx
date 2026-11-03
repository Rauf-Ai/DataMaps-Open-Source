import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

// ============== Утилиты-хуки ==============

function useInView(threshold = 0.2) {
  const ref = useRef(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ob = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setInView(true); ob.disconnect(); } },
      { threshold },
    );
    ob.observe(el);
    return () => ob.disconnect();
  }, [threshold]);
  return [ref, inView];
}

// ============== TypeWriter ==============

const TYPE_PHRASES = [
  "Найди 500 салонов красоты в Москве с рейтингом выше 4.5 и сайтом",
  "Я продаю кассовое оборудование — покажи все кафе и рестораны Казани без сайта",
  "Выгрузи всех стоматологов Краснодарского края с email и телефоном",
  "Строю дома под ключ в Татарстане — найди всех моих конкурентов",
  "Покажи автосервисы Екатеринбурга с более чем 100 отзывами",
];

function TypeWriter({ phrases = TYPE_PHRASES, typingSpeed = 60, deletingSpeed = 25, pauseAfterType = 2500, pauseAfterDelete = 800 }) {
  const [text, setText] = useState("");
  const [phraseIdx, setPhraseIdx] = useState(0);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const current = phrases[phraseIdx];
    let t;
    if (!deleting && text === current) {
      t = setTimeout(() => setDeleting(true), pauseAfterType);
    } else if (deleting && text === "") {
      t = setTimeout(() => {
        setDeleting(false);
        setPhraseIdx((i) => (i + 1) % phrases.length);
      }, pauseAfterDelete);
    } else if (!deleting) {
      t = setTimeout(() => setText(current.slice(0, text.length + 1)), typingSpeed);
    } else {
      t = setTimeout(() => setText(current.slice(0, text.length - 1)), deletingSpeed);
    }
    return () => clearTimeout(t);
  }, [text, deleting, phraseIdx, phrases, typingSpeed, deletingSpeed, pauseAfterType, pauseAfterDelete]);

  return (
    <span>
      {text}
      <span className="inline-block w-[2px] h-[1em] align-middle ml-1 bg-brand animate-blink" />
    </span>
  );
}

// ============== CountUp ==============

function easeOutExpo(t) { return t === 1 ? 1 : 1 - Math.pow(2, -10 * t); }

function CountUp({ target, duration = 2000, suffix = "" }) {
  const [ref, inView] = useInView(0.4);
  const [value, setValue] = useState(0);

  useEffect(() => {
    if (!inView) return;
    let raf;
    const start = performance.now();
    const tick = (now) => {
      const p = Math.min(1, (now - start) / duration);
      setValue(Math.floor(easeOutExpo(p) * target));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [inView, target, duration]);

  const fmt = new Intl.NumberFormat("ru-RU").format(value);
  return <span ref={ref}>{fmt}{suffix}</span>;
}

// ============== LiveCounter ==============

function LiveCounter() {
  const [n, setN] = useState(() => 10000 + Math.floor(Math.random() * 5000));
  useEffect(() => {
    let timer;
    const schedule = () => {
      const delay = 3000 + Math.random() * 5000;
      timer = setTimeout(() => {
        setN((v) => v + 1 + Math.floor(Math.random() * 5));
        schedule();
      }, delay);
    };
    schedule();
    return () => clearTimeout(timer);
  }, []);
  return <span className="font-bold text-brand-dark">{new Intl.NumberFormat("ru-RU").format(n)}</span>;
}

// ============== Reveal wrapper ==============

function Reveal({ children, delay = 0, className = "" }) {
  const [ref, inView] = useInView(0.15);
  return (
    <div
      ref={ref}
      className={`transition-all duration-700 ease-out ${className}`}
      style={{
        opacity: inView ? 1 : 0,
        transform: inView ? "translateY(0)" : "translateY(30px)",
        transitionDelay: `${delay}ms`,
      }}
    >
      {children}
    </div>
  );
}

// ============== Иконки (мини-SVG) ==============

const IconSearch = (props) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
    <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
  </svg>
);
const IconSparkles = (props) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
    <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
  </svg>
);
const IconCheck = (props) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" {...props}>
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

// ============== Logo ==============

const LogoText = ({ className = "" }) => (
  <span className={`font-manrope font-extrabold text-2xl tracking-tight ${className}`}>
    <span className="font-medium">Data</span><span className="font-extrabold">Maps</span>
    <span className="inline-block w-2 h-2 rounded-full bg-brand ml-1 align-middle" />
  </span>
);

// ============== Sub-components ==============

const UseCase = ({ icon, title, text }) => (
  <div className="bg-white rounded-2xl p-6 shadow-sm hover:shadow-xl transition-all duration-300 hover:-translate-y-1 border border-gray-100">
    <div className="text-4xl mb-3">{icon}</div>
    <h3 className="font-manrope font-bold text-lg text-ink mb-2">{title}</h3>
    <p className="text-muted text-sm leading-relaxed">{text}</p>
  </div>
);

const Stat = ({ value, label, suffix = "" }) => (
  <div className="text-center">
    <div className="font-manrope font-extrabold text-5xl md:text-6xl text-brand tracking-tight">
      <CountUp target={value} suffix={suffix} />
    </div>
    <div className="text-muted text-sm mt-2 font-dmsans">{label}</div>
  </div>
);

const Avatar = ({ name, color }) => {
  const initials = name.split(" ").map((s) => s[0]).slice(0, 2).join("");
  return (
    <div className={`w-12 h-12 rounded-full flex items-center justify-center font-manrope font-bold text-white text-base ${color}`}>
      {initials}
    </div>
  );
};

const Testimonial = ({ name, role, color, text }) => (
  <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-100">
    <div className="flex items-center gap-3 mb-4">
      <Avatar name={name} color={color} />
      <div>
        <div className="font-manrope font-bold text-ink">{name}</div>
        <div className="text-muted text-xs">{role}</div>
      </div>
    </div>
    <div className="text-brand text-sm mb-3">★★★★★</div>
    <p className="text-ink/80 text-sm leading-relaxed font-dmsans">{text}</p>
  </div>
);

const PriceCard = ({ name, credits, price, perUnit, oldPerUnit, popular }) => (
  <div className={`rounded-2xl p-6 transition-all duration-300 ${
    popular
      ? "bg-white border-2 border-brand shadow-xl md:scale-105 relative"
      : "bg-white border border-gray-100 shadow-sm hover:shadow-md"
  }`}>
    {popular && (
      <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-brand text-white text-xs font-bold px-3 py-1 rounded-full font-manrope tracking-wide">
        ПОПУЛЯРНЫЙ
      </div>
    )}
    <div className="font-manrope font-bold text-lg text-ink mb-1">{name}</div>
    <div className="text-muted text-sm mb-4 font-dmsans">{new Intl.NumberFormat("ru-RU").format(credits)} кредитов</div>
    <div className="font-manrope font-extrabold text-3xl text-ink mb-1">
      {new Intl.NumberFormat("ru-RU").format(price)} ₽
    </div>
    <div className="text-sm mb-5 font-dmsans">
      {oldPerUnit && <span className="text-muted line-through mr-2">{oldPerUnit} ₽</span>}
      <span className="text-muted">{perUnit} ₽ за организацию</span>
    </div>
    <button className={`w-full py-3 rounded-xl font-manrope font-bold text-sm transition-all ${
      popular
        ? "bg-brand text-white hover:bg-brand-dark"
        : "bg-brand-light text-brand-dark hover:bg-brand hover:text-white"
    }`}>
      Выбрать
    </button>
  </div>
);

// ============== Главный компонент ==============

export function LandingPage() {
  const nav = useNavigate();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [navScrolled, setNavScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setNavScrolled(window.scrollY > 20);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const scrollTo = (id) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
    setMobileMenuOpen(false);
  };

  const goToRegister = () => nav("/register");
  const goToLogin = () => nav("/login");

  return (
    <div className="min-h-screen font-dmsans text-ink bg-paper relative overflow-x-hidden">
      {/* Декоративный фон: сетка + контур карты России */}
      <div className="absolute inset-0 pointer-events-none opacity-[0.04]"
           style={{
             backgroundImage: "linear-gradient(#1A1A2E 1px, transparent 1px), linear-gradient(90deg, #1A1A2E 1px, transparent 1px)",
             backgroundSize: "48px 48px",
           }} />

      {/* ====== NAV ====== */}
      <nav className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        navScrolled ? "bg-white/90 backdrop-blur-md shadow-sm" : "bg-transparent"
      }`}>
        <div className="max-w-7xl mx-auto px-4 md:px-8 py-4 flex items-center justify-between">
          <button onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} className="flex items-center">
            <LogoText />
          </button>
          <div className="hidden md:flex items-center gap-8">
            <button onClick={() => scrollTo("ai-demo")} className="text-ink hover:text-brand font-medium text-sm transition-colors">
              ИИ-помощник
            </button>
            <button onClick={() => scrollTo("pricing")} className="text-ink hover:text-brand font-medium text-sm transition-colors">
              Тарифы
            </button>
            <button onClick={() => scrollTo("use-cases")} className="text-ink hover:text-brand font-medium text-sm transition-colors">
              Кому подходит
            </button>
            <button onClick={goToLogin} className="text-ink hover:text-brand font-medium text-sm transition-colors">
              Войти
            </button>
            <button onClick={goToRegister} className="bg-brand text-white px-5 py-2.5 rounded-xl font-manrope font-bold text-sm hover:bg-brand-dark transition-all hover:scale-[1.03]">
              Начать поиск →
            </button>
          </div>
          <button className="md:hidden text-ink" onClick={() => setMobileMenuOpen((v) => !v)} aria-label="меню">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              {mobileMenuOpen
                ? <path d="M18 6 6 18M6 6l12 12" />
                : <path d="M3 12h18M3 6h18M3 18h18" />}
            </svg>
          </button>
        </div>
        {mobileMenuOpen && (
          <div className="md:hidden bg-white border-t border-gray-100 px-4 py-4 flex flex-col gap-3">
            <button onClick={() => scrollTo("ai-demo")} className="text-left text-ink py-2">ИИ-помощник</button>
            <button onClick={() => scrollTo("pricing")} className="text-left text-ink py-2">Тарифы</button>
            <button onClick={() => scrollTo("use-cases")} className="text-left text-ink py-2">Кому подходит</button>
            <button onClick={goToLogin} className="text-left text-ink py-2">Войти</button>
            <button onClick={goToRegister} className="bg-brand text-white px-5 py-3 rounded-xl font-bold mt-2">Начать поиск →</button>
          </div>
        )}
      </nav>

      {/* ====== HERO ====== */}
      <section className="relative pt-24 pb-12 md:pt-40 md:pb-28 px-4 md:px-8">
        <div className="max-w-5xl mx-auto text-center relative z-10">
          <Reveal>
            <div className="inline-flex items-center gap-2 bg-brand-light text-brand-dark text-xs font-bold px-3 py-1.5 rounded-full mb-6 font-manrope tracking-wide">
              <IconSparkles className="w-4 h-4" /> ИИ-ПОМОЩНИК ВНУТРИ
            </div>
          </Reveal>

          <Reveal delay={100}>
            <h1 className="font-manrope font-black text-3xl md:text-6xl lg:text-7xl text-ink leading-[1.05] tracking-tight mb-6">
              Найдите клиентов среди <span className="text-brand">5 млн организаций</span> России за 2 минуты
            </h1>
          </Reveal>

          <Reveal delay={200}>
            <p className="text-muted text-base md:text-xl max-w-2xl mx-auto mb-7 md:mb-10 font-dmsans leading-relaxed">
              Опишите кого ищете — ИИ-помощник найдёт, отфильтрует и выгрузит готовую базу в Excel
            </p>
          </Reveal>

          <Reveal delay={300}>
            <div className="max-w-3xl mx-auto bg-white rounded-2xl shadow-xl border border-gray-100 p-4 md:p-5 flex items-center gap-3 text-left">
              <IconSparkles className="w-6 h-6 text-brand shrink-0" />
              <div className="flex-1 font-dmsans text-ink text-base md:text-lg min-h-[2.5em] md:min-h-[3em] flex items-center">
                <TypeWriter />
              </div>
            </div>
            <div className="text-xs text-muted mt-3">Или используйте фильтры вручную →</div>
          </Reveal>

          <Reveal delay={400}>
            <div className="mt-7 md:mt-10 flex flex-col items-center">
              <button
                onClick={goToRegister}
                className="bg-brand text-white px-8 py-4 rounded-2xl font-manrope font-bold text-lg flex items-center gap-3 hover:bg-brand-dark transition-all hover:scale-[1.03] shadow-lg shadow-brand/25"
              >
                <IconSearch className="w-5 h-5" /> Начать поиск
              </button>
              <div className="text-xs text-muted mt-3">20 контактов бесплатно · Без карты</div>
            </div>
          </Reveal>
        </div>
      </section>

      {/* ====== LIVE COUNTER ====== */}
      <div className="bg-gradient-to-r from-brand-light/60 via-brand-light to-brand-light/60 border-y border-brand/15 py-4">
        <div className="max-w-4xl mx-auto px-4 text-center text-sm md:text-base text-ink font-dmsans">
          📊 Сегодня выгружено: <LiveCounter /> организаций
        </div>
      </div>

      {/* ====== AI DEMO ====== */}
      <section id="ai-demo" className="py-20 md:py-28 px-4 md:px-8 relative">
        <div className="max-w-6xl mx-auto">
          <Reveal>
            <div className="text-center mb-14">
              <h2 className="font-manrope font-extrabold text-3xl md:text-5xl text-ink mb-4 tracking-tight">
                Просто опишите задачу — ИИ сделает остальное
              </h2>
              <p className="text-muted text-base md:text-lg max-w-2xl mx-auto">
                Не нужно разбираться в фильтрах. Напишите что ищете обычным языком
              </p>
            </div>
          </Reveal>

          <div className="grid md:grid-cols-2 gap-6 mb-14">
            <Reveal>
              <div className="bg-white rounded-2xl p-6 shadow-md border border-gray-100">
                <div className="text-xs text-muted uppercase tracking-wide font-bold mb-4">Чат с ИИ</div>
                <div className="space-y-4">
                  <div className="flex justify-end">
                    <div className="bg-brand-light text-ink px-4 py-3 rounded-2xl rounded-tr-md max-w-[85%] text-sm">
                      Найди мне фитнес-клубы Новосибирска с хорошими отзывами и сайтом
                    </div>
                  </div>
                  <div className="flex items-start gap-2">
                    <div className="w-8 h-8 rounded-full bg-brand text-white flex items-center justify-center shrink-0">
                      <IconSparkles className="w-4 h-4" />
                    </div>
                    <div className="bg-gray-50 px-4 py-3 rounded-2xl rounded-tl-md max-w-[85%] text-sm font-dmsans">
                      <div className="font-bold text-ink mb-2">Нашёл <span className="text-brand">847</span> фитнес-клубов в Новосибирске</div>
                      <div className="text-muted text-xs mb-3">Применил фильтры:</div>
                      <ul className="space-y-1.5 text-xs text-ink">
                        <li className="flex items-center gap-2"><IconCheck className="w-4 h-4 text-green-600" /> Категория: Фитнес-клуб</li>
                        <li className="flex items-center gap-2"><IconCheck className="w-4 h-4 text-green-600" /> Город: Новосибирск</li>
                        <li className="flex items-center gap-2"><IconCheck className="w-4 h-4 text-green-600" /> Рейтинг: от 4.0</li>
                        <li className="flex items-center gap-2"><IconCheck className="w-4 h-4 text-green-600" /> Есть сайт: да</li>
                        <li className="flex items-center gap-2"><IconCheck className="w-4 h-4 text-green-600" /> Отзывов: от 10</li>
                      </ul>
                      <div className="mt-3 text-xs text-brand font-bold">Готово к выгрузке — 847 организаций</div>
                    </div>
                  </div>
                </div>
              </div>
            </Reveal>

            <Reveal delay={150}>
              <div className="bg-white rounded-2xl p-4 shadow-md border border-gray-100 overflow-hidden">
                <div className="text-xs text-muted uppercase tracking-wide font-bold mb-3 px-2">Превью результата</div>
                <div className="relative">
                  <table className="w-full text-sm font-dmsans">
                    <thead>
                      <tr className="text-left text-xs text-muted border-b border-gray-100">
                        <th className="py-2 px-2 font-medium">Название</th>
                        <th className="py-2 px-2 font-medium">Рейтинг</th>
                        <th className="py-2 px-2 font-medium">Отзывов</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr className="border-b border-gray-50">
                        <td className="py-2 px-2 font-medium text-ink">World Class</td>
                        <td className="py-2 px-2 text-ink">4.8</td>
                        <td className="py-2 px-2 text-muted">512</td>
                      </tr>
                      <tr className="border-b border-gray-50">
                        <td className="py-2 px-2 font-medium text-ink">Алекс Фитнес</td>
                        <td className="py-2 px-2 text-ink">4.6</td>
                        <td className="py-2 px-2 text-muted">328</td>
                      </tr>
                      <tr className="border-b border-gray-50">
                        <td className="py-2 px-2 font-medium text-ink">SportLife NSK</td>
                        <td className="py-2 px-2 text-ink">4.5</td>
                        <td className="py-2 px-2 text-muted">241</td>
                      </tr>
                      {[1, 2, 3, 4].map((i) => (
                        <tr key={i} className="border-b border-gray-50" style={{ filter: "blur(6px)" }}>
                          <td className="py-2 px-2">████████ ███</td>
                          <td className="py-2 px-2">█.█</td>
                          <td className="py-2 px-2">███</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-white via-white/90 to-transparent flex items-end justify-center pb-2">
                    <div className="text-sm font-bold text-brand-dark">+ ещё 844 организации</div>
                  </div>
                </div>
              </div>
            </Reveal>
          </div>

          <div className="grid md:grid-cols-3 gap-4">
            {[
              { icon: "🏪", title: "Найди поставщиков", text: "«Все оптовые базы стройматериалов в Самарской области» — готовый список за минуту" },
              { icon: "🎯", title: "Найди конкурентов", text: "«Мои конкуренты в нише пластиковых окон в Москве» — для анализа рынка" },
              { icon: "📞", title: "Собери базу для обзвона", text: "«Кафе и рестораны Казани без сайта с телефоном» — 100% таргет" },
            ].map((c, i) => (
              <Reveal key={c.title} delay={i * 100}>
                <div className="bg-white rounded-2xl p-5 border border-gray-100">
                  <div className="text-2xl mb-2">{c.icon}</div>
                  <div className="font-manrope font-bold text-ink mb-1">{c.title}</div>
                  <div className="text-muted text-sm">{c.text}</div>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* ====== USE CASES ====== */}
      <section id="use-cases" className="py-20 md:py-28 px-4 md:px-8 bg-white">
        <div className="max-w-6xl mx-auto">
          <Reveal>
            <h2 className="font-manrope font-extrabold text-3xl md:text-5xl text-ink mb-14 text-center tracking-tight">
              DataMaps помогает <span className="text-brand">находить клиентов</span>
            </h2>
          </Reveal>

          <div className="grid md:grid-cols-3 gap-5">
            {[
              { icon: "📞", title: "Менеджеры по продажам", text: "Готовая база для холодных звонков с телефонами и email" },
              { icon: "📊", title: "Маркетологи", text: "Сегментируйте рынок по регионам, рейтингу, категориям" },
              { icon: "🎯", title: "Рекламные агентства", text: "Выгружайте целевые базы для клиентов за минуты" },
              { icon: "💻", title: "Фрилансеры", text: "Найдите тех, кому нужны ваши услуги — без сайта, без соцсетей" },
              { icon: "✉️", title: "SMS/Email рассылки", text: "Точные контакты для таргетированных рассылок" },
              { icon: "📈", title: "Аналитики", text: "Исследуйте рынок: кол-во игроков, рейтинги, покрытие" },
            ].map((u, i) => (
              <Reveal key={u.title} delay={(i % 3) * 100}>
                <UseCase {...u} />
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* ====== STATS ====== */}
      <section className="py-20 md:py-24 px-4 md:px-8 bg-paper">
        <div className="max-w-6xl mx-auto">
          <Reveal>
            <h2 className="font-manrope font-extrabold text-3xl md:text-5xl text-ink mb-14 text-center tracking-tight">
              База данных всей России
            </h2>
          </Reveal>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
            <Reveal><Stat value={5200000} suffix="+" label="организаций" /></Reveal>
            <Reveal delay={100}><Stat value={1554} label="категорий бизнеса" /></Reveal>
            <Reveal delay={200}><Stat value={85} label="регионов России" /></Reveal>
            <Reveal delay={300}><Stat value={350000} suffix="+" label="городов и нас. пунктов" /></Reveal>
          </div>
          <div className="text-center text-xs text-muted mt-10 font-dmsans">
            Источник: Яндекс Карты · Обновлено: май 2026
          </div>
        </div>
      </section>

      {/* ====== SAMPLE XLSX ====== */}
      <section className="py-20 md:py-28 px-4 md:px-8 bg-white">
        <div className="max-w-6xl mx-auto">
          <Reveal>
            <h2 className="font-manrope font-extrabold text-3xl md:text-5xl text-ink mb-14 text-center tracking-tight">
              Что вы получите в <span className="text-brand">Excel-файле</span>
            </h2>
          </Reveal>
          <Reveal>
            <div className="rounded-2xl shadow-xl overflow-hidden border border-gray-200 bg-white relative">
              <div className="bg-[#107c41] text-white px-4 py-2 text-sm font-bold flex items-center gap-2">
                <span className="w-3 h-3 bg-white/30 rounded-sm" /> spravochnik_mebel_kazan_2026-05-21.xlsx
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm font-dmsans min-w-[800px]">
                  <thead>
                    <tr className="bg-gray-50 text-xs text-muted border-b border-gray-200">
                      <th className="text-left py-3 px-3 font-bold">Название</th>
                      <th className="text-left py-3 px-3 font-bold">Город</th>
                      <th className="text-left py-3 px-3 font-bold">Телефон</th>
                      <th className="text-left py-3 px-3 font-bold">Сайт</th>
                      <th className="text-left py-3 px-3 font-bold">Рейтинг</th>
                      <th className="text-left py-3 px-3 font-bold">Отзывов</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ["Мебель на заказ «Уют»", "Казань", "+7 843 555-1234", "uyut-kazan.ru", "4.7", "234"],
                      ["СтройМастер", "Казань", "+7 843 555-5678", "stroiymast.ru", "4.5", "189"],
                      ["Кухни Люкс", "Казань", "+7 843 555-9012", "—", "4.8", "312"],
                    ].map((row, i) => (
                      <tr key={i} className="border-b border-gray-100 hover:bg-gray-50/50">
                        {row.map((cell, j) => (
                          <td key={j} className="py-3 px-3 text-ink">{cell}</td>
                        ))}
                      </tr>
                    ))}
                    {[1, 2].map((i) => (
                      <tr key={`b-${i}`} className="border-b border-gray-100" style={{ filter: "blur(6px)" }}>
                        <td className="py-3 px-3">████████ ████ «████»</td>
                        <td className="py-3 px-3">██████</td>
                        <td className="py-3 px-3">+7 ███ ███-████</td>
                        <td className="py-3 px-3">████████.██</td>
                        <td className="py-3 px-3">█.█</td>
                        <td className="py-3 px-3">███</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="bg-brand-light border-t-2 border-brand/30 px-4 py-4 text-center">
                <span className="font-bold text-ink">+ ещё 1 247 организаций</span>
                <button onClick={goToRegister} className="ml-3 text-brand-dark font-bold underline underline-offset-2 hover:no-underline">
                  Выгрузить всё →
                </button>
              </div>
            </div>
            <div className="text-center text-xs text-muted mt-4 font-dmsans">
              Колонки: Название, Город, Адрес, Телефон, Сайт, Email, Рейтинг, Отзывов, Telegram, WhatsApp
            </div>
          </Reveal>
        </div>
      </section>

      {/* ====== PRICING ====== */}
      <section id="pricing" className="py-20 md:py-28 px-4 md:px-8 bg-paper">
        <div className="max-w-7xl mx-auto">
          <Reveal>
            <div className="text-center mb-14">
              <h2 className="font-manrope font-extrabold text-3xl md:text-5xl text-ink mb-4 tracking-tight">
                Платите только за <span className="text-brand">нужные контакты</span>
              </h2>
              <p className="text-muted text-base md:text-lg">
                1 кредит = 1 организация в выгрузке. Чем больше — тем дешевле
              </p>
            </div>
          </Reveal>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-5">
            <Reveal><PriceCard name="Старт"    credits={100}   price={290}   perUnit="2,90" oldPerUnit="3,50" /></Reveal>
            <Reveal delay={80}><PriceCard name="Базовый"  credits={500}   price={990}   perUnit="1,98" oldPerUnit="2,50" /></Reveal>
            <Reveal delay={160}><PriceCard name="Профи"    credits={2000}  price={2990}  perUnit="1,50" popular /></Reveal>
            <Reveal delay={240}><PriceCard name="Бизнес"   credits={10000} price={9900}  perUnit="0,99" /></Reveal>
            <Reveal delay={320}><PriceCard name="Максимум" credits={50000} price={29900} perUnit="0,60" /></Reveal>
          </div>
          <div className="text-center mt-10 text-sm text-muted">
            ✨ <span className="text-ink font-bold">20 контактов бесплатно</span> при регистрации
          </div>
        </div>
      </section>

      {/* ====== TESTIMONIALS ====== */}
      <section className="py-20 md:py-28 px-4 md:px-8 bg-white">
        <div className="max-w-6xl mx-auto">
          <Reveal>
            <h2 className="font-manrope font-extrabold text-3xl md:text-5xl text-ink mb-14 text-center tracking-tight">
              Нам доверяют
            </h2>
          </Reveal>
          <div className="grid md:grid-cols-3 gap-5">
            <Reveal>
              <Testimonial
                name="Алексей М."
                role="Руководитель отдела продаж, Москва"
                color="bg-blue-500"
                text="Раньше менеджеры тратили по 2 дня на сбор базы для обзвона. Теперь за 10 минут выгружаем 500 контактов с телефонами и сайтами. Окупили подписку за первый день."
              />
            </Reveal>
            <Reveal delay={100}>
              <Testimonial
                name="Динара К."
                role="Маркетолог, Казань"
                color="bg-rose-500"
                text="ИИ-помощник — огонь. Написала: нужны все кафе Казани без сайта. Через минуту получила 1200 контактов. Теперь продаём им разработку сайтов."
              />
            </Reveal>
            <Reveal delay={200}>
              <Testimonial
                name="Сергей В."
                role="Владелец рекламного агентства, Новосибирск"
                color="bg-emerald-500"
                text="Клиент попросил базу автосервисов по Сибири. Раньше это неделя работы. С DataMaps — 5 минут. Клиент в шоке, мы в плюсе."
              />
            </Reveal>
          </div>
        </div>
      </section>

      {/* ====== CTA FINAL ====== */}
      <section className="py-20 md:py-28 px-4 md:px-8 bg-gradient-to-br from-brand-light via-paper to-brand-light">
        <Reveal>
          <div className="max-w-3xl mx-auto text-center">
            <h2 className="font-manrope font-extrabold text-3xl md:text-5xl text-ink mb-6 tracking-tight">
              Начните бесплатно — за 60 секунд
            </h2>
            <p className="text-muted text-base md:text-lg mb-8">
              20 контактов в подарок · Без привязки карты · ИИ-помощник доступен сразу
            </p>
            <button
              onClick={goToRegister}
              className="bg-brand text-white px-8 py-4 rounded-2xl font-manrope font-bold text-lg inline-flex items-center gap-3 hover:bg-brand-dark transition-all hover:scale-[1.03] shadow-lg shadow-brand/25"
            >
              <IconSearch className="w-5 h-5" /> Начать поиск
            </button>
          </div>
        </Reveal>
      </section>

      {/* ====== FOOTER ====== */}
      <footer className="bg-ink text-white py-12 px-4 md:px-8">
        <div className="max-w-6xl mx-auto">
          <div className="flex flex-col md:flex-row items-center justify-between gap-6 mb-6">
            <div>
              <LogoText className="text-white" />
              <div className="text-white/60 text-sm mt-1">Поиск организаций России с ИИ</div>
            </div>
            <div className="flex flex-wrap items-center gap-6 text-sm">
              <button onClick={goToRegister} className="hover:text-brand transition-colors">Начать поиск</button>
              <button onClick={() => scrollTo("pricing")} className="hover:text-brand transition-colors">Тарифы</button>
              <a href="mailto:hello@datamaps.ru" className="hover:text-brand transition-colors">Контакты</a>
            </div>
          </div>
          <div className="border-t border-white/10 pt-6 text-xs text-white/50 flex flex-col md:flex-row items-center justify-between gap-2">
            <div>© 2026 DataMaps.ru</div>
            <div>Источник данных: Яндекс Карты</div>
          </div>
        </div>
      </footer>
    </div>
  );
}
