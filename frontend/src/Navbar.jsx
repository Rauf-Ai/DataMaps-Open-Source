import { useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { Button, Label, Text } from "@gravity-ui/uikit";
import { Moon, Sun } from "@gravity-ui/icons";
import { useAuth } from "./AuthContext.jsx";
import { useAppTheme } from "./main.jsx";
import { Logo } from "./Logo.jsx";
import { fmtInt } from "./api.js";
import { LOCAL_MODE } from "./localMode.js";

export function Navbar() {
  const { theme, toggle: toggleTheme } = useAppTheme();
  const { me, loading, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);

  const closeMenu = () => setMenuOpen(false);

  const authLinks = me ? (
    <>
      <Label theme="info" size="m" className="dm-navbar__credits">
        {LOCAL_MODE ? "Локально · бесплатно" : `Кредиты: ${fmtInt(me.credits_balance)}`}
      </Label>
      <NavLink to="/dashboard" onClick={closeMenu}>
        {({ isActive }) => (
          <Button view={isActive ? "outlined" : "flat"} size="m">
            Кабинет
          </Button>
        )}
      </NavLink>
      <NavLink to="/analysis" onClick={closeMenu}>
        {({ isActive }) => (
          <Button view={isActive ? "outlined" : "flat"} size="m">
            Анализ компаний
          </Button>
        )}
      </NavLink>
      <NavLink to="/downloads" onClick={closeMenu}>
        {({ isActive }) => (
          <Button view={isActive ? "outlined" : "flat"} size="m">
            История выгрузок
          </Button>
        )}
      </NavLink>
      {me.is_admin && (
        <NavLink to="/admin" onClick={closeMenu}>
          {({ isActive }) => (
            <Button view={isActive ? "outlined" : "flat"} size="m">
              Админка
            </Button>
          )}
        </NavLink>
      )}
      <Button view="flat" size="m" onClick={() => { closeMenu(); logout(); }}>
        Выйти
      </Button>
    </>
  ) : (
    <>
      <NavLink to="/login" onClick={closeMenu}>
        <Button view="flat" size="m">Войти</Button>
      </NavLink>
      <NavLink to="/register" onClick={closeMenu}>
        <Button view="action" size="m">Регистрация</Button>
      </NavLink>
    </>
  );

  return (
    <header className="dm-navbar">
      <div className="dm-navbar__inner">
        <Link to="/" className="dm-logo" onClick={closeMenu}>
          <Logo className="dm-logo__img" />
        </Link>
        <div className="dm-navbar__right">
          <Button
            view="flat"
            size="m"
            onClick={toggleTheme}
            title={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
          {!loading && (
            <div className="dm-navbar__desktop">
              {authLinks}
            </div>
          )}
          {!loading && (
            <Button
              view="outlined"
              size="m"
              className="dm-navbar__menu-btn"
              onClick={() => setMenuOpen((v) => !v)}
              aria-label={menuOpen ? "Закрыть меню" : "Открыть меню"}
            >
              {menuOpen ? "×" : "☰"}
            </Button>
          )}
        </div>
      </div>
      {!loading && menuOpen && (
        <nav className="dm-navbar__mobile">
          {authLinks}
        </nav>
      )}
    </header>
  );
}
