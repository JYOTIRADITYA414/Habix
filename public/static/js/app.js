(() => {
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];

  // Responsive navigation and dismissible notices.
  const sidebar = $("#sidebar");
  const toggle = $("#menu-toggle");
  const scrim = $("#sidebar-scrim");
  const closeMenu = () => { sidebar?.classList.remove("sidebar-open"); scrim?.classList.remove("scrim-open"); };
  toggle?.addEventListener("click", () => { sidebar.classList.toggle("sidebar-open"); scrim?.classList.toggle("scrim-open"); });
  scrim?.addEventListener("click", closeMenu);
  $$(".flash-close").forEach(button => button.addEventListener("click", () => button.closest(".flash")?.remove()));
  $$('form[data-confirm]').forEach(form => form.addEventListener("submit", event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  }));

  // Chart.js is optional for offline use; the data tables and summaries stay available.
  const chartColors = () => {
    const style = getComputedStyle(document.documentElement);
    return { text: style.getPropertyValue("--muted").trim() || "#7d8393", grid: style.getPropertyValue("--border").trim() || "#eceef3", accent: "#776cf2" };
  };
  if (window.Chart) {
    Chart.defaults.font.family = "'DM Sans', sans-serif";
    Chart.defaults.color = chartColors().text;
    Chart.defaults.plugins.legend.display = false;
    const weekly = window.HABIX_CHARTS?.weekly;
    if (weekly && $("#weeklyStudyChart")) {
      new Chart($("#weeklyStudyChart"), { type: "bar", data: { labels: weekly.labels, datasets: [{ data: weekly.values, backgroundColor: weekly.labels.map((_, i) => i === 6 ? "#7569ed" : "#dedbff"), hoverBackgroundColor: "#7569ed", borderRadius: 7, maxBarThickness: 32 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { tooltip: { callbacks: { label: item => `${item.raw} study hours` } } }, scales: { x: { grid: { display: false }, border: { display: false } }, y: { beginAtZero: true, suggestedMax: Math.max(2, ...weekly.values), ticks: { precision: 0 }, grid: { color: chartColors().grid }, border: { display: false } } } } });
    }
    const analytics = window.HABIX_ANALYTICS;
    if (analytics && $("#studyHoursChart")) {
      new Chart($("#studyHoursChart"), { type: "line", data: { labels: analytics.labels, datasets: [{ label: "Recorded", data: analytics.hours, borderColor: "#776cf2", backgroundColor: "rgba(119,108,242,.12)", fill: true, tension: .36, pointRadius: 3, pointHoverRadius: 5, pointBackgroundColor: "#776cf2" }, { label: "Planned", data: analytics.planned || [], borderColor: "#b9bdca", backgroundColor: "transparent", borderDash: [5, 4], fill: false, tension: .3, pointRadius: 2, pointHoverRadius: 4, pointBackgroundColor: "#b9bdca" }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: true, position: "bottom" }, tooltip: { callbacks: { label: item => `${item.dataset.label}: ${item.raw} hours` } } }, scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxTicksLimit: 10 } }, y: { beginAtZero: true, grid: { color: chartColors().grid }, border: { display: false } } } } });
    }
    if (analytics?.subjects?.length && $("#subjectChart")) {
      new Chart($("#subjectChart"), { type: "doughnut", data: { labels: analytics.subjects, datasets: [{ data: analytics.subjectHours, backgroundColor: ["#776cf2", "#40b9a3", "#f2a45f", "#60a5d9", "#e47e9a", "#9d85d8", "#93a4b8", "#e6c45d"], borderWidth: 3, borderColor: getComputedStyle(document.documentElement).getPropertyValue("--surface") || "#fff", hoverOffset: 6 }] }, options: { responsive: true, maintainAspectRatio: false, cutout: "70%" } });
    }
  }

  // Focus session control. The server stores active/paused state so a refresh can recover it.
  const focus = $("#focus-app");
  if (focus) {
    let active = window.HABIX_ACTIVE_SESSION;
    let breakUntil = 0;
    let breakFinished = false;
    let finishing = false;
    const display = $("#timer-display");
    const ring = $("#timer-ring");
    const mode = $("#timer-mode");
    const subtitle = $("#timer-subtitle");
    const stateLabel = $("#timer-state-label");
    const message = $("#timer-message");
    const setup = $("#timer-setup");
    const startButton = $("#timer-start");
    const pauseButton = $("#timer-pause");
    const resumeButton = $("#timer-resume");
    const stopButton = $("#timer-stop");
    const csrf = $('input[name="csrf_token"]', focus)?.value || $("input[name='csrf_token']")?.value || "";
    const duration = Number(active?.planned_minutes || 25) * 60;
    let startedMilliseconds = active?.started_at ? Date.parse(active.started_at) : 0;
    const format = seconds => `${String(Math.floor(Math.max(0, seconds) / 60)).padStart(2, "0")}:${String(Math.max(0, seconds) % 60).padStart(2, "0")}`;
    const elapsed = () => (Number(active?.elapsed_seconds || 0) + (active?.state === "active" && startedMilliseconds ? Math.max(0, Math.floor((Date.now() - startedMilliseconds) / 1000)) : 0));
    const syncButtons = () => {
      setup.hidden = !!active;
      startButton.hidden = !!active;
      pauseButton.hidden = !active || active.state !== "active";
      resumeButton.hidden = !active || active.state !== "paused";
      stopButton.hidden = !active;
      mode.textContent = active ? "FOCUS SESSION" : breakUntil && !breakFinished ? "BREAK TIME" : "FOCUS SESSION";
      stateLabel.textContent = active?.state === "paused" ? "SESSION PAUSED" : active ? "SESSION IN PROGRESS" : breakUntil && !breakFinished ? "A MOMENT TO RESET" : "READY WHEN YOU ARE";
      if (active) subtitle.textContent = active.subject;
      ring.classList.toggle("timer-paused", active?.state === "paused");
    };
    const call = async action => {
      const body = new URLSearchParams({ action });
      const response = await fetch("/study/control", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRF-Token": csrf }, body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Timer action failed.");
      return data;
    };
    const setMessage = (text, error = false) => { message.textContent = text; message.classList.toggle("message-error", error); };
    const finishFocus = async () => {
      if (!active || finishing) return;
      finishing = true;
      try {
        const data = await call("stop");
        active = null;
        startedMilliseconds = 0;
        const breakMinutes = Number(focus.dataset.breakMinutes || 10);
        breakUntil = Date.now() + breakMinutes * 60 * 1000;
        breakFinished = false;
        setMessage(`Session saved · ${data.actual_minutes} focused minute${data.actual_minutes === 1 ? "" : "s"}. Take a ${breakMinutes}-minute break.`);
        subtitle.textContent = "Your focus session has been recorded.";
        syncButtons();
      } catch (error) { setMessage(error.message, true); }
      finally { finishing = false; }
    };
    const tick = () => {
      if (active) {
        const remaining = Math.max(0, Number(active.planned_minutes) * 60 - elapsed());
        display.textContent = format(remaining);
        ring.style.setProperty("--timer-progress", `${Math.min(100, elapsed() / (Number(active.planned_minutes) * 60) * 100)}%`);
        if (remaining === 0 && active.state === "active") finishFocus();
      } else if (breakUntil && !breakFinished) {
        const remaining = Math.ceil((breakUntil - Date.now()) / 1000);
        display.textContent = format(remaining);
        ring.style.setProperty("--timer-progress", `${Math.max(0, 100 - remaining / (Number(focus.dataset.breakMinutes || 10) * 60) * 100)}%`);
        if (remaining <= 0) { breakFinished = true; display.textContent = "00:00"; mode.textContent = "BREAK COMPLETE"; stateLabel.textContent = "READY FOR ANOTHER BLOCK"; subtitle.textContent = "Nice reset. Start your next focus block when you’re ready."; setMessage("Break complete. Take another minute if you need it."); }
      } else if (!active) display.textContent = `${String(Number($("#focus-minutes")?.value === "custom" ? $("#custom-minutes")?.value : $("#focus-minutes")?.value || 25)).padStart(2, "0")}:00`;
    };
    startButton?.addEventListener("click", async () => {
      const subject = $("#focus-subject").value.trim();
      const choice = $("#focus-minutes").value;
      const minutes = choice === "custom" ? Number($("#custom-minutes").value) : Number(choice);
      if (!subject) { setMessage("Add a subject or task before you start.", true); $("#focus-subject").focus(); return; }
      try {
        const body = new URLSearchParams({ action: "start", subject, planned_minutes: String(minutes) });
        const response = await fetch("/study/control", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRF-Token": csrf }, body });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Could not start the timer.");
        active = data; startedMilliseconds = Date.parse(data.started_at); breakUntil = 0; breakFinished = false; setMessage(""); syncButtons(); tick();
      } catch (error) { setMessage(error.message, true); }
    });
    pauseButton?.addEventListener("click", async () => { try { active = { ...active, ...await call("pause") }; startedMilliseconds = 0; syncButtons(); tick(); } catch (error) { setMessage(error.message, true); } });
    resumeButton?.addEventListener("click", async () => { try { active = { ...active, ...await call("resume") }; startedMilliseconds = Date.parse(active.started_at); syncButtons(); tick(); } catch (error) { setMessage(error.message, true); } });
    stopButton?.addEventListener("click", finishFocus);
    $("#focus-minutes")?.addEventListener("change", event => { $("#custom-minutes").hidden = event.target.value !== "custom"; tick(); });
    syncButtons(); tick(); window.setInterval(tick, 1000);
  }
})();
