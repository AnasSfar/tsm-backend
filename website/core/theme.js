import { state } from "./state.js";

/* =========================
   THEME
========================= */

export function clearThemeVariables() {
  const root = document.documentElement.style;

  [
    "--bg","--bg-2","--surface","--surface-2","--surface-3",
    "--text","--muted","--line","--accent","--accent-2",
    "--hover-border","--shadow","--shadow-soft","--shadow-hover"
  ].forEach(p => root.removeProperty(p));
}

export function applyLightTheme(){
  document.body.dataset.theme = "light";
  clearThemeVariables();
}

export function applyDarkTheme(){
  document.body.dataset.theme = "dark";
}

export async function applyTheme(mode = state.themeMode){

  state.themeMode = mode;
  localStorage.setItem("site-theme-mode", mode);

  mode === "dark"
    ? applyDarkTheme()
    : applyLightTheme();

}


/* =========================
   THEME SWITCHER UI
========================= */

export function renderThemeSwitcher(){

  const label = state.themeMode === "dark" ? "Dark" : "Light";

  return `
  <div class="theme-switcher" id="themeSwitcher">

    <button
      id="themeToggleBtn"
      class="theme-toggle-btn"
      type="button"
    >
      Theme · ${label}
    </button>

    <div class="theme-menu" id="themeMenu">

      <button
        class="theme-option ${state.themeMode==="light"?"active":""}"
        data-theme="light"
      >
        Light
      </button>

      <button
        class="theme-option ${state.themeMode==="dark"?"active":""}"
        data-theme="dark"
      >
        Dark
      </button>

    </div>

  </div>
  `;
}


/**
 * @param {() => void} [onThemeChange] - callback to run after theme switches (e.g. renderPage)
 */
export function bindThemeSwitcher(onThemeChange){

  const toggle = document.getElementById("themeToggleBtn");
  const switcher = document.getElementById("themeSwitcher");
  const menu = document.getElementById("themeMenu");

  if(!toggle || !switcher || !menu) return;
  if(switcher.dataset.bound==="1") return;

  switcher.dataset.bound="1";

  toggle.onclick = e=>{
    e.stopPropagation();
    switcher.classList.toggle("open");
  };

  menu.querySelectorAll(".theme-option").forEach(btn=>{
    btn.onclick = async ()=>{
      switcher.classList.remove("open");
      await applyTheme(btn.dataset.theme);
      onThemeChange?.();
    };
  });

  document.onclick = e=>{
    if(!switcher.contains(e.target)){
      switcher.classList.remove("open");
    }
  };

}
