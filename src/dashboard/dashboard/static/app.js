// ===== DABOM 대시보드 화면 스크립트 =====
// index.html 안에 있던 <script> 블록을 그대로 옮긴 것이다.
// 클래식 스크립트(모듈 아님)로 불러오므로 여기 선언한 함수는 전역이 되고,
// 마크업의 onclick="..." 이 그대로 찾을 수 있다. 순서를 바꾸거나 모듈로 바꾸면 깨진다.

// ===== HTML 조립 =====
// 이 파일의 표·목록은 문자열을 조립해 innerHTML에 한 번에 넣는다. 그 문자열에 환자 이름·메모처럼
// 사람이 입력한 값이 그대로 섞이면, 값 안의 <, > 가 태그로 해석돼 실행된다(저장형 XSS).
// 예: 환자 이름을 <img src=x onerror=...> 로 등록하면 관리자가 목록을 열 때 그 코드가 돈다.
//
// 그래서 이런 문자열은 h`...` 로 만든다. 백틱 앞에 함수 이름을 붙이는 태그드 템플릿(ES6 표준)이라
// ${...} 안의 값이 전부 자동으로 이스케이프된다. 나중에 열을 추가할 때 따로 신경 쓸 게 없다.
//
//     tr.innerHTML = h`<td>${w.name}</td>`;
//
// 태그(<td>)는 코드에 직접 쓴 글자라 그대로 남고 값만 안전해진다.
// 브라우저가 &lt; 를 다시 < 로 그려주므로 정상 데이터의 화면 출력은 이스케이프 전과 같다.
//
// 이미 만들어 둔 HTML 조각(칩, 버튼 묶음 등)을 끼워 넣을 때는 raw(...) 로 감싼다.
// 감싸는 걸 잊으면 태그가 글자로 보이므로 화면에서 바로 눈에 띈다 — 조용히 취약해지지 않는다.
function esc(v) {
    if (v === null || v === undefined) return "";
    return String(v)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function raw(s) { return { __html: s === null || s === undefined ? "" : String(s) }; }

function h(parts, ...vals) {
    return parts.reduce((out, s, i) => {
        if (i >= vals.length) return out + s;
        const v = vals[i];
        return out + s + (v && v.__html !== undefined ? v.__html : esc(v));
    }, "");
}

function updateClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    const wd = ["일", "월", "화", "수", "목", "금", "토"][now.getDay()];
    const dateStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')} (${wd})`;
    const aClock = document.getElementById("a-hero-clock");
    if (aClock) aClock.innerText = `${dateStr}  ${h}:${m}:${s}`;
    const monClock = document.getElementById("mon-clock");
    if (monClock) monClock.innerText = `${h}:${m}:${s}`;
}
setInterval(updateClock, 1000);
updateClock();

// ===== 상단 탭 전환 (SPA: JS show/hide) =====
function showTab(name) {
    // 권한 없는 탭은 접근 차단 → 역할 홈으로
    if (!tabAllowed(name)) name = homeTab();
    const panel = document.getElementById("tab-" + name);
    if (!panel) return;
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    document.querySelectorAll(".navlink").forEach(b => b.classList.remove("active"));
    panel.classList.add("active");
    const link = document.querySelector(`.navlink[data-tab="${name}"]`);
    if (link) link.classList.add("active");
    window.scrollTo(0, 0);
    updateNav();
    if (name === "schedule") loadSchedule();
    // 관리자 탭들은 로그인 시점 캐시가 오래됐을 수 있어 탭을 열 때마다 다시 불러온다
    if (name === "events") renderEvents();
    if (name === "patients") { renderPatients(); }
    if (name === "monitoring") {
        renderRoomRiskPanel();
        if (monLogTab === "fall") { loadFallLogTab(); }
    }
    if (name === "astats") { renderCharts(); renderFalls(); }
    if (name === "ahome") { renderCharts(); renderFalls(); }
    if (name === "accounts") { renderPending(); renderGuardians(); }
    // 보호자 탭들도 마찬가지 — 순찰 기록·낙상 알림은 로그인 이후 새로 쌓였을 수 있다
    if (name === "ghome" || name === "myroom" || name === "myinfo") renderGuardian();
    if (name === "gstats") renderCharts();
    // 숨은 패널은 크기가 0이라 등장 판정에서 늘 제외된다.
    // 이미 맨 위에 있으면 scrollTo가 scroll 이벤트를 내지 않으므로 여기서 직접 다시 판정한다.
    refreshReveal();
}

// 상단바: 맨 위 = 투명(배경 일체), 스크롤 시 = 프로스티드 띠
// 홈 히어로(건물 사진) 위에서는 흰 글자(over-hero) 유지, 건물이 사라지면 어두운 글자
function updateNav() {
    const tb = document.getElementById("topbar");
    if (!tb) return;
    const atTop = window.scrollY < 40;
    // 상단바 뒤에 어두운 사진(홈 건물 / 병실 사진)이 충분히 진하게 깔려 있으면 투명·흰 글자 유지
    const heroBg = document.querySelector(".tab-panel.active .js-dark-under-nav");
    let overHero = false;
    if (heroBg) {
        const r = heroBg.getBoundingClientRect();
        overHero = r.bottom > 70 && parseFloat(getComputedStyle(heroBg).opacity) > 0.55;
    }
    tb.classList.toggle("solid", !atTop && !overHero);
    tb.classList.toggle("over-hero", overHero);
}
window.addEventListener("scroll", updateNav);

// 히어로 스크롤 연동
//   맨 위      : 건물 사진만 (문구 숨김)
//   살짝 내리면 : .hero-in 을 붙여 문구가 한 번에 부드럽게 올라오게 함 (전환은 CSS 담당)
//   더 내리면   : 문구가 내려가고, 사진이 점점 투명해지며 아래 섹션이 올라옴
function initHeroScroll() {
    const tile = document.getElementById("lp-hero-tile");
    const stage = tile && tile.querySelector(".lp-hero-stage");
    if (!tile || !stage) return;

    const ramp = (p, a, b) => Math.min(1, Math.max(0, (p - a) / (b - a)));

    function apply() {
        const range = tile.offsetHeight - window.innerHeight; // sticky가 붙어 있는 거리
        // 히어로가 화면보다 낮은 예외 상황(창 높이 측정 불가 등)에서는 사진+문구를 그냥 노출
        if (range <= 0) {
            stage.classList.add("hero-in");
            stage.classList.remove("hero-past");
            tile.style.setProperty("--hero-bg-o", "1");
            updateNav();
            return;
        }
        const p = Math.min(1, Math.max(0, window.scrollY / range));

        // 문구: 스크롤이 시작되면 켜고, 아래 섹션이 올라올 즈음 끔 (전환은 CSS 0.85s)
        stage.classList.toggle("hero-in", p > 0.04 && p < 0.66);
        stage.classList.toggle("hero-past", p >= 0.66);   // 스크롤 안내는 다시 띄우지 않음

        // 사진: 0.58부터 옅어지되 완전히 지우지 않고 0.22 정도로 남김
        tile.style.setProperty("--hero-bg-o", (1 - 0.78 * ramp(p, 0.58, 1)).toFixed(3));
        updateNav();   // 상단바 색은 사진이 얼마나 진하게 남았는지를 보고 결정
    }

    apply();
    window.addEventListener("scroll", apply, { passive: true });
    window.addEventListener("resize", apply);
}

// ===== 로그인 화면 =====
// 탭은 인증 방식이 아니라 '누가 쓰는 화면인지'를 고른다.
// 고른 구분에 따라 아이디 placeholder와 오른쪽 안내만 바뀌고, 로그인 자체는 동일하다.
const LOGIN_ROLES = {
    admin: {
        placeholder: "간호사 아이디",
        title: "간호사 계정으로 로그인하면",
        items: [
            "전체 병실의 실시간 영상과 순찰 로봇 위치를 확인합니다.",
            "순찰 시작·정지·복귀와 비상 정지를 원격으로 제어합니다.",
            "낙상 통계와 이벤트 기록을 열람하고 대응 상태를 관리합니다.",
            "간호사·보호자 계정을 만들고 권한을 관리합니다."
        ],
        note: "간호사 계정은 관리자가 직접 발급합니다."
    },
    guardian: {
        placeholder: "보호자 아이디",
        title: "보호자 계정으로 로그인하면",
        items: [
            "담당 병실의 순찰 로봇 위치와 환자 상태를 확인합니다.",
            "환자 상태와 최근 순찰 기록을 확인합니다.",
            "낙상이 감지되면 알림을 받습니다."
        ],
        note: '매핑 코드를 받으셨나요?<br>' +
              '<button class="apply-link" onclick="closeLogin(); openRedeem();">코드로 계정 만들기</button><br>' +
              '아직 신청 전이라면<br> ' +
              '<button class="apply-link" onclick="closeLogin(); openApply();">보호자 연동 신청하기</button>'
    }
};

let loginRole = "admin";

function setLoginRole(role) {
    const r = LOGIN_ROLES[role];
    if (!r) return;
    loginRole = role;

    document.getElementById("login-id").value = "";
    document.getElementById("login-pw").value = "";

    document.querySelectorAll(".login-tab").forEach(b => {
        b.classList.toggle("active", b.dataset.role === role);
    });
    document.getElementById("login-id").placeholder = r.placeholder;
    document.getElementById("login-aside-title").innerText = r.title;

    // 관리자(간호사)일 때만 보안 배너·경고 문구를 노출한다
    const isAdmin = role === "admin";
    document.getElementById("login-overlay").classList.toggle("login-admin", isAdmin);

    const list = document.getElementById("login-aside-list");
    list.innerHTML = "";
    r.items.forEach(text => {
        const li = document.createElement("li");
        li.innerText = text;
        list.appendChild(li);
    });

    document.getElementById("login-aside-note").innerHTML = r.note;
}

function openLogin() {
    setLoginRole(loginRole);
    document.getElementById("login-error").innerText = "";
    document.getElementById("login-overlay").classList.add("open");
    document.getElementById("login-id").focus();
}

function closeLogin() {
    document.getElementById("login-overlay").classList.remove("open");
}

// ===== 입력 형식 보정 =====
// 숫자만 받는 칸과 전화번호 하이픈은 입력하는 즉시 맞춰준다.
// 이름처럼 한글이 들어가는 칸에는 쓰지 않는다 — 한글 조합 중에 값을 고치면 입력이 깨진다.
function onlyDigits(el, max) {
    el.value = el.value.replace(/\D/g, "").slice(0, max);
}

function formatPhone(el) {
    const d = el.value.replace(/\D/g, "").slice(0, 11);
    el.value = d.length > 7 ? `${d.slice(0, 3)}-${d.slice(3, 7)}-${d.slice(7)}`
             : d.length > 3 ? `${d.slice(0, 3)}-${d.slice(3)}`
             : d;
}

// ===== 보호자 연동 신청 =====
// 제출하면 관리자 '승인 대기'(PENDING)에 쌓인다. 계정과 매핑 코드는 승인 시점에 발급된다.
function openApply() {
    document.getElementById("apply-error").innerText = "";
    document.getElementById("apply-cols").style.display = "";
    document.getElementById("apply-done").style.display = "none";
    ["apply-name", "apply-phone", "apply-patient", "apply-room"]
        .forEach(id => { document.getElementById(id).value = ""; });
    document.getElementById("apply-overlay").classList.add("open");
    document.getElementById("apply-name").focus();
}

function closeApply() {
    document.getElementById("apply-overlay").classList.remove("open");
}

async function submitApply() {
    const val = id => document.getElementById(id).value.trim();
    const name = val("apply-name");
    const phone = val("apply-phone");
    const patient = val("apply-patient");
    const room = val("apply-room");
    const err = document.getElementById("apply-error");

    if (name.length < 2) {
        err.innerText = "신청자 이름을 2자 이상 입력해 주세요.";
        return;
    }
    if (!/^01[016789]-\d{3,4}-\d{4}$/.test(phone)) {
        err.innerText = "연락처를 010-0000-0000 형식으로 끝까지 입력해 주세요.";
        return;
    }
    if (patient.length < 2) {
        err.innerText = "환자 이름을 2자 이상 입력해 주세요.";
        return;
    }
    if (!/^\d{3,4}$/.test(room)) {
        err.innerText = "병실 번호를 숫자 3~4자리로 입력해 주세요.";
        return;
    }

    try {
        const res = await fetch("/api/apply", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, phone, patient, room })
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            err.innerText = data.error || "신청 처리 중 오류가 발생했습니다.";
            return;
        }
    } catch (e) {
        err.innerText = "서버와 통신할 수 없습니다.";
        return;
    }

    renderPending();   // 관리자 화면을 열어두지 않아도 목록에 반영해 둔다

    err.innerText = "";
    ["apply-name", "apply-phone", "apply-patient", "apply-room"]
        .forEach(id => { document.getElementById(id).value = ""; });

    document.getElementById("apply-cols").style.display = "none";
    document.getElementById("apply-done").style.display = "block";
}

// ===== 코드 등록 =====
// 승인된 매핑 코드를 확인한 뒤, 보호자가 아이디·비밀번호를 직접 정해 계정을 만든다.
// 병원은 코드만 발급하므로 비밀번호를 알지 못한다.
let redeemEntry = null;

function showRedeemStep(step) {
    document.getElementById("redeem-step1").style.display = step === 1 ? "grid" : "none";
    document.getElementById("redeem-step2").style.display = step === 2 ? "grid" : "none";
    document.getElementById("redeem-done").style.display = step === 3 ? "block" : "none";
}

function openRedeem() {
    redeemEntry = null;
    ["redeem-code", "redeem-id", "redeem-pw", "redeem-pw2"]
        .forEach(id => { document.getElementById(id).value = ""; });
    document.getElementById("redeem-error").innerText = "";
    document.getElementById("redeem-error2").innerText = "";
    showRedeemStep(1);
    document.getElementById("redeem-overlay").classList.add("open");
    document.getElementById("redeem-code").focus();
}

function closeRedeem() {
    document.getElementById("redeem-overlay").classList.remove("open");
}

async function verifyCode() {
    const code = document.getElementById("redeem-code").value.trim().toUpperCase();
    const err = document.getElementById("redeem-error");

    if (!code) { err.innerText = "매핑 코드를 입력해 주세요."; return; }

    let data;
    try {
        const res = await fetch("/api/verify-code", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code })
        });
        data = await res.json();
        if (!res.ok || !data.ok) {
            err.innerText = data.error || "승인된 코드를 찾을 수 없습니다. 문자로 받은 코드를 확인해 주세요.";
            return;
        }
    } catch (e) {
        err.innerText = "서버와 통신할 수 없습니다.";
        return;
    }

    redeemEntry = { code, name: data.name, patient: data.patient };
    err.innerText = "";
    document.getElementById("redeem-linked").innerHTML =
        '<span class="redeem-linked-label">연결될 환자</span>' +
        h`<strong>${data.patient}</strong>` +
        h`<span class="redeem-linked-sub">신청자 ${data.name} · 코드 ${code}</span>`;
    showRedeemStep(2);
    document.getElementById("redeem-id").focus();
}

async function submitRedeem() {
    const id = document.getElementById("redeem-id").value.trim();
    const pw = document.getElementById("redeem-pw").value;
    const pw2 = document.getElementById("redeem-pw2").value;
    const err = document.getElementById("redeem-error2");

    if (!redeemEntry) { showRedeemStep(1); return; }
    if (id.length < 4) { err.innerText = "아이디는 4자 이상 입력해 주세요."; return; }
    if (pw.length < 4) { err.innerText = "비밀번호는 4자 이상 입력해 주세요."; return; }
    if (pw !== pw2) { err.innerText = "비밀번호가 서로 다릅니다."; return; }

    try {
        const res = await fetch("/api/redeem", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: redeemEntry.code, username: id, password: pw })
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            err.innerText = data.error || "계정 생성 중 오류가 발생했습니다.";
            return;
        }
    } catch (e) {
        err.innerText = "서버와 통신할 수 없습니다.";
        return;
    }

    renderPending();
    renderGuardians();

    document.getElementById("redeem-done-msg").innerHTML =
        h`<b>${redeemEntry.patient}</b> 보호자로 연동되었습니다.<br>` +
        h`아이디 <b>${id}</b> 로 로그인하시면 담당 병실을 확인할 수 있습니다.`;
    err.innerText = "";
    showRedeemStep(3);
}

// 스크롤 시 섹션이 페이드+슬라이드업으로 등장 (스크롤 위치 기반 · 확실)
const REVEAL_SEL =
    "#tab-home .lp-h2, #tab-home .lp-sub, #tab-home .feat-carousel, " +
    "#tab-home .lp-stat, #tab-home .lp-btn, #tab-home .lp-footer-inner, " +
    "#tab-rooms .lp-h2, #tab-rooms .lp-sub, #tab-rooms .rm-svc-card, " +
    "#tab-about .lp-h2, #tab-about .lp-sub, #tab-about .ab-problem, " +
    "#tab-about .ab-solution, #tab-about .ab-tier, #tab-about .ab-video, " +
    "#tab-about .lp-btn, " +
    "#tab-features .fx-overview li, #tab-features .fx-text, " +
    "#tab-features .fx-visual, #tab-features .lp-btn";

// 병실을 바꿔 카드를 다시 그린 뒤에도 애니메이션이 걸리도록 밖에서 호출한다
let refreshReveal = () => {};

function initReveal() {
    let els = [];
    document.documentElement.classList.add("reveal-on");

    // 뷰포트에 들어오면 표시, 벗어나면 다시 숨김 → 재진입 때마다 애니메이션 반복
    function check() {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        els.forEach(el => {
            const r = el.getBoundingClientRect();
            const inView = r.top < vh * 0.85 && r.bottom > vh * 0.08;
            el.classList.toggle("in", inView);
        });
    }

    refreshReveal = function () {
        els = [...document.querySelectorAll(REVEAL_SEL)];
        els.forEach(e => e.classList.add("reveal"));
        check();
    };

    refreshReveal();
    window.addEventListener("scroll", check, { passive: true });
    window.addEventListener("resize", check);
}

// ===== 기능 카드 캐러셀 =====
// 카드마다 현재 위치(data-pos: -1 왼쪽 뒤 / 0 정면 / 1 오른쪽 뒤)를 붙이면
// 실제 회전·블러는 CSS가 처리한다. 5초마다 한 칸씩 돌고, 손이 닿으면 멈춘다.
let featIdx = 0;
let featCards = [];
let featTimer = null;

function featLayout() {
    const n = featCards.length;
    featCards.forEach((card, i) => {
        let pos = ((i - featIdx) % n + n) % n;
        if (pos > n / 2) pos -= n;          // 0,1,2 → -1,0,1
        card.dataset.pos = pos;
        card.setAttribute("aria-hidden", pos === 0 ? "false" : "true");
        card.tabIndex = pos === 0 ? 0 : -1;
    });
    document.querySelectorAll("#feat-dots button").forEach((dot, i) => {
        dot.classList.toggle("active", i === featIdx);
    });
}

function featRestart() {
    clearInterval(featTimer);
    featTimer = setInterval(() => featMove(1), 5000);
}

function featGo(i) {
    featIdx = (i + featCards.length) % featCards.length;
    featLayout();
    featRestart();
}

function featMove(step) { featGo(featIdx + step); }

function initFeatures() {
    const wrap = document.getElementById("feat-carousel");
    const stage = document.getElementById("feat-stage");
    const dots = document.getElementById("feat-dots");
    if (!wrap || !stage || !dots) return;

    featCards = [...stage.querySelectorAll(".feat-card")];

    featCards.forEach((card, i) => {
        card.onclick = () => { if (card.dataset.pos !== "0") featGo(i); };
        const dot = document.createElement("button");
        dot.type = "button";
        dot.setAttribute("aria-label", `${i + 1}번째 기능 보기`);
        dot.onclick = () => featGo(i);
        dots.appendChild(dot);
    });

    // 읽는 중에는 돌지 않도록
    wrap.addEventListener("mouseenter", () => clearInterval(featTimer));
    wrap.addEventListener("mouseleave", featRestart);
    wrap.addEventListener("focusin", () => clearInterval(featTimer));
    wrap.addEventListener("focusout", featRestart);

    wrap.addEventListener("keydown", e => {
        if (e.key === "ArrowLeft") featMove(-1);
        if (e.key === "ArrowRight") featMove(1);
    });

    // 모바일: 좌우 스와이프
    let touchX = null;
    stage.addEventListener("touchstart", e => { touchX = e.touches[0].clientX; }, { passive: true });
    stage.addEventListener("touchend", e => {
        if (touchX === null) return;
        const dx = e.changedTouches[0].clientX - touchX;
        if (Math.abs(dx) > 40) featMove(dx < 0 ? 1 : -1);
        touchX = null;
    });

    featLayout();
    featRestart();
}

// ===== 병실 소개 =====
// 아이콘은 1인실 서비스 시트(rooms/room1-svc.png) 한 장에서 잘라 쓰고 색을 반전시켜
// 세 병실 모두 같은 남색 톤으로 통일한다. (ico-* 클래스의 좌표는 style.css 참고)
const ROOMS = {
    r4: {
        name: "4인실",
        photo: "rooms/room4-photo.png",
        // 사진 하단 평균색 rgb(136,123,114)에서 뽑은 이음색 (진한 쪽 45% / 옅은 쪽 8%)
        blend: "#c9c4c0", tint: "#f6f4f4",
        tagline: "쾌적하고 안전한 환경에서 함께하는 회복의 공간",
        sub: "환자의 빠른 회복과 편안한 입원 생활을 위해 KJ병원이 세심하게 준비했습니다.",
        svc: [
            { ico: "bed",       t: "쾌적한 입원 환경",     d: "넓고 깨끗한 병실 공간과<br>개별 커튼으로 편안한 휴식 보장" },
            { ico: "shield",    t: "24시간 전문 의료 케어", d: "전문의·간호 인력의<br>신속하고 체계적인 진료 및 모니터링" },
            { ico: "food",      t: "영양 맞춤형 식사 제공", d: "환자 상태에 맞춘<br>균형 잡힌 건강식 식단 제공" },
            { ico: "tv",        t: "편의시설 지원",        d: "개인 침상별 TV, 냉장고,<br>전원·USB 등 편리한 생활 환경 제공" },
            { ico: "care",      t: "안전한 병원 생활",     d: "낙상 방지 시스템과 정기적 순찰로<br>안전하고 안심할 수 있는 입원 환경" }
        ]
    },
    r2: {
        name: "2인실",
        photo: "rooms/room2-photo.png",
        // 사진 하단 평균색 rgb(136,113,90)
        blend: "#c9bfb5", tint: "#f6f4f2",
        tagline: "프라이버시와 품격을 고려한 더 편안하고, 더 집중되는 회복의 공간",
        sub: "더 세심한 케어와 다양한 편의 서비스를 통해 입원 기간 동안 최상의 만족을 드립니다.",
        svc: [
            { ico: "bed",       t: "쾌적한 입원 환경",      d: "넓고 프라이빗한 공간과 개별 냉·난방 시스템,<br>고급 침구류 제공" },
            { ico: "shield",    t: "24시간 전문 의료 케어",  d: "전문의·간호 인력의 1:1 맞춤 케어와<br>신속하고 체계적인 진료 및 모니터링" },
            { ico: "food",      t: "맞춤형 식단 서비스",     d: "영양사와 상담 후 환자 상태에 맞춘<br>프리미엄 맞춤 식단을 1:1 제공" },
            { ico: "concierge", t: "전담 컨시어지 서비스",   d: "입원 안내, 예약, 외부 업무 대행 등<br>전담 직원의 맞춤 지원" },
            { ico: "lounge",    t: "보호자 편의 시설",      d: "보호자 전용 휴게 공간, 리클라이너 침대,<br>개별 수납장 및 샤워실 제공" },
            { ico: "tv",        t: "첨단 편의시설 지원",     d: "스마트 TV, 개인 태블릿, 고속 Wi-Fi,<br>무선 충전기, 공기청정기 등 최신 시설 제공" },
            { ico: "car",       t: "주차 및 발렛 서비스",    d: "보호자 전용 주차 공간 및<br>발렛 서비스 제공" },
            { ico: "care",      t: "안전 & 케어 서비스",     d: "낙상 감지 시스템, 비상 호출 시스템 등<br>안전 관리 강화" }
        ]
    },
    r1: {
        name: "1인실 (VIP)",
        photo: "rooms/room1-photo.png",
        // 사진 하단 평균색 rgb(86,63,44)
        blend: "#b3a9a0", tint: "#f2f0ee",
        tagline: "가장 높은 수준의 케어를 제공하는 프라이빗 VIP 공간",
        sub: "호텔식 환경과 전담 케어로 입원 기간 내내 최상의 편안함을 제공합니다.",
        svc: [
            { ico: "bed",       t: "최고급 입원 환경",      d: "호텔식 프라이빗 인테리어,<br>최상급 침구류 제공" },
            { ico: "shield",    t: "24시간 전담 의료 케어",  d: "전문의·간호 전문팀의<br>1:1 맞춤 케어" },
            { ico: "food",      t: "프리미엄 식사 서비스",   d: "맞춤형 영양 식단,<br>셰프의 프리미엄 메뉴" },
            { ico: "concierge", t: "전담 컨시어지 서비스",   d: "입원 안내, 예약, 일정 관리<br>및 각종 요청 지원" },
            { ico: "lounge",    t: "VIP 휴게 공간",         d: "환자 및 보호자를 위한<br>프라이빗 라운지" },
            { ico: "tv",        t: "스마트 엔터테인먼트",    d: "85인치 TV, OTT, 고속 Wi-Fi,<br>블루투스 사운드 시스템" },
            { ico: "car",       t: "발렛 & 전용 주차",       d: "전용 주차 공간 및<br>발렛 서비스 제공" },
            { ico: "care",      t: "통합 케어 & 보안",       d: "24시간 보안, 감염 관리,<br>정기 케어 프로그램" }
        ]
    }
};

let currentRoom = "r4";

function showRoom(key) {
    const room = ROOMS[key];
    if (!room) return;
    currentRoom = key;

    document.querySelectorAll(".rm-tab").forEach(b => {
        b.classList.toggle("active", b.dataset.room === key);
    });

    // 사진에서 뽑은 이음색을 탭 전체에 뿌려 히어로 아래와 서비스 섹션이 같은 색으로 이어지게
    const panel = document.getElementById("tab-rooms");
    panel.style.setProperty("--rm-blend", room.blend);
    panel.style.setProperty("--rm-tint", room.tint);

    document.getElementById("rm-photo").style.backgroundImage = `url('${room.photo}')`;
    document.getElementById("rm-title").innerText = room.name;
    document.getElementById("rm-tagline").innerText = room.tagline;
    document.getElementById("rm-svc-room").innerText = room.name;
    document.getElementById("rm-svc-sub").innerText = room.sub;

    const grid = document.getElementById("rm-svc-grid");
    // 카드 개수에 맞춰 열 수를 정한다 (5개 = 한 줄, 8개 = 4+4)
    grid.className = "rm-svc-grid n" + room.svc.length;
    grid.innerHTML = "";
    room.svc.forEach(s => {
        const card = document.createElement("div");
        card.className = "rm-svc-card";
        card.innerHTML =
            `<span class="svc-ico ico-${s.ico}"></span>` +
            `<h3>${s.t}</h3>` +
            `<p>${s.d}</p>`;
        grid.appendChild(card);
    });

    // 새로 그린 카드에도 스크롤 등장 애니메이션을 다시 걸어준다
    refreshReveal();
}

// 병실 사진만 크게 보기 (잘림 없이 사진 전체)
function openRoomPhoto() {
    const room = ROOMS[currentRoom];
    document.getElementById("rm-lightbox-img").src = room.photo;
    document.getElementById("rm-lightbox-cap").innerText = room.name + " 병실";
    document.getElementById("rm-lightbox").classList.add("open");
}

function closeRoomPhoto() {
    document.getElementById("rm-lightbox").classList.remove("open");
}

document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    closeRoomPhoto();
    closeLogin();
    closeApply();
    closeRedeem();
    closeConfirm();
});

// ===== 로그인 / 권한 (테스트용: admin·cus / 1234, DB 미사용) =====
const AUTH_KEY = "dashboard-auth";
const ROLE_KEY = "dabom-role";
const NAME_KEY = "dabom-name";
const USER_KEY = "dabom-user";   // 로그인 아이디 (연동된 환자를 찾는 키)

/* ---- 역할 값 변환 (DB ↔ 화면) ----------------------------------
   DB(users.role) : "admin" = 관리자·간호사 / "user" = 보호자
   화면 내부      : "admin"                / "guardian"
   API 응답을 받는 곳에서 toAppRole() 한 번만 통과시키면 되고,
   DB 로 보낼 때는 toDbRole() 을 쓴다. 대소문자는 알아서 맞춘다. */
const ROLE_DB_TO_APP = { admin: "admin", nurse: "admin", user: "guardian", guardian: "guardian" };
const ROLE_APP_TO_DB = { admin: "admin", guardian: "user" };

function toAppRole(dbRole) {
    return ROLE_DB_TO_APP[String(dbRole || "").toLowerCase()] || "guest";
}
function toDbRole(appRole) {
    return ROLE_APP_TO_DB[String(appRole || "").toLowerCase()] || "user";
}

// 역할별 상단 메뉴 (id = 탭 패널 id 접미사)
const NAV = {
    guest: [
        { id: "home", label: "Home" },
        { id: "about", label: "시스템 소개" },
        { id: "features", label: "주요 기능" },
        { id: "rooms", label: "병실 소개" }
    ],
    guardian: [
        { id: "ghome", label: "Home" },
        { id: "myroom", label: "내 병실" },
        { id: "glive", label: "실시간 병실" },
        { id: "gstats", label: "통계" },
        { id: "myinfo", label: "내 정보" }
    ],
    admin: [
        { id: "ahome", label: "Home" },
        { id: "monitoring", label: "병실 모니터링" },
        { id: "patients", label: "환자 관리" },
        { id: "robot", label: "Robot Control" },
        { id: "astats", label: "통계" },
        { id: "events", label: "이벤트" },
        { id: "schedule", label: "일정" },
        { id: "accounts", label: "계정 관리" }
    ]
};

function currentRole() {
    const saved = localStorage.getItem(ROLE_KEY) || sessionStorage.getItem(ROLE_KEY);
    return saved ? toAppRole(saved) : "guest";
}
function currentName() {
    return localStorage.getItem(NAME_KEY) || sessionStorage.getItem(NAME_KEY) || "";
}
function currentUser() {
    return localStorage.getItem(USER_KEY) || sessionStorage.getItem(USER_KEY) || "";
}
function homeTab() { return NAV[currentRole()][0].id; }
function goHome() { showTab(homeTab()); }
function tabAllowed(name) { return NAV[currentRole()].some(t => t.id === name); }

// 역할에 맞는 상단 메뉴 + 우측(로그인/계정) 렌더
function renderNav() {
    const role = currentRole();
    const nav = document.getElementById("topnav");
    const right = document.getElementById("topbar-right");
    if (!nav || !right) return;
    nav.innerHTML = "";
    NAV[role].forEach(t => {
        const b = document.createElement("button");
        b.className = "navlink";
        b.dataset.tab = t.id;
        b.textContent = t.label;
        b.onclick = () => showTab(t.id);
        nav.appendChild(b);
    });
    if (role === "guest") {
        right.innerHTML =
            '<button class="nav-login" onclick="openLogin()">로그인</button>' +
            '<button class="nav-signup" onclick="openApply()">연동 신청</button>';
    } else {
        right.innerHTML =
            h`<span class="nav-user">${currentName()}</span>` +
            '<button class="nav-signup nav-logout" onclick="logout()">로그아웃</button>';
    }
}

// ===== 관리자 화면 =====
// 병실별 위험도 태그 (실서비스에서는 환자 위험도 API로 대체)
// 환자 정보 원본 — 관리자 '환자 관리'에서 등록·수정하고,
// 병실 모니터링 그리드가 이 값을 그대로 쓴다
const RISK_TEXT = { critical: "매우높음", high: "높음", mid: "보통", low: "낮음" };

// 실제 환자(patients 테이블)의 위험도는 DB에 한글 그대로 저장된다 ("낮음"/"보통"/"높음"/"매우 높음").
// risk-chip 색상 클래스만 여기서 매핑해 쓴다.
const RISK_CLASS = { "낮음": "low", "보통": "mid", "높음": "high", "매우 높음": "critical" };

// ===== 병실 구성 — 이 표 하나가 원본 =====
// 병원 구조가 고정이라 매핑으로 처리한다. 유형·정원이 화면 여러 곳에 흩어져 있으면
// 서로 어긋나므로(실제로 어긋난 적 있음) 여기만 고치면 전부 따라오게 한다.
//   - 현재 위치 목록 / 로봇 제어 도면 라벨  -> syncRoomLabels()
//   - 환자 등록 병실 선택지               -> renderRoomOptions()
//   - 등록 정원 검사                      -> roomCapacityOf()
const ROOM_INFO = {
    "101": { type: "1인실", capacity: 1 },
    "102": { type: "2인실", capacity: 2 },
    "103": { type: "2인실", capacity: 2 },
    "104": { type: "4인실", capacity: 4 },
};
const ROOM_NUMBERS = Object.keys(ROOM_INFO);
const roomTypeOf = (roomNo) => (ROOM_INFO[String(roomNo)] || {}).type || "-";
const roomCapacityOf = (roomNo) => (ROOM_INFO[String(roomNo)] || {}).capacity;

// 환자 등록 병실 선택지 (101~104호만 고를 수 있게 한다)
function renderRoomOptions() {
    const sel = document.getElementById("pt-room");
    if (!sel) { return; }
    sel.innerHTML = '<option value="">선택</option>' + ROOM_NUMBERS.map(no =>
        `<option value="${no}">${no}호 (${ROOM_INFO[no].type})</option>`
    ).join("");
}

// 현재 위치 목록·로봇 제어 도면의 "n인실" 라벨을 ROOM_INFO에 맞춘다.
// 도면 쪽은 병실 번호를 RC_PLACES(=rooms.yaml)에서 받으므로 rcLoadPlaces()가 한 번 더 부른다
function syncRoomLabels() {
    ROOM_NUMBERS.forEach(no => {
        const cell = document.querySelector(`#room-${no} span`);
        if (cell) { cell.innerText = ROOM_INFO[no].type; }
    });
    document.querySelectorAll(".rc-spot").forEach(spot => {
        const info = ROOM_INFO[(RC_PLACES[spot.dataset.place] || {}).room_number];
        const sub = info && spot.querySelector(".rc-sub");
        if (sub) { sub.textContent = info.type; }
    });
}

// 재원 환자 목록 (환자관리 탭 · 이벤트 환자 지정이 함께 사용)
let WARDS = [];

function toWard(p) {
    return {
        id: p.id,
        room: p.room_number,
        name: p.name,
        age: p.age,
        sex: p.sex || "-",
        diagnosis: p.disease || "-",
        risk: p.risk_level || "낮음",
        lastFall: p.last_fall || "낙상 이력 없음"
    };
}

async function fetchWards() {
    try {
        const res = await fetch("/api/patients");
        const data = await res.json();
        return data.ok ? data.patients.map(toWard) : [];
    } catch (e) {
        return [];
    }
}

// ----- 이벤트: 캡처 확인 · 대응 완료 -----
let evSelected = null;

async function openEvCapture(id) {
    const e = FALL_LOGS.find(f => f.id === id);
    if (!e) return;
    evSelected = id;
    document.getElementById("ev-capture-time").innerText = e.detected_at;
    document.getElementById("ev-capture-room").innerText = "병실 " + e.room_number;
    document.getElementById("ev-capture-type").innerText = "낙상";
    document.getElementById("ev-capture-tag").innerText = "낙상 확정 프레임";
    document.getElementById("ev-memo").value = e.memo || "";
    document.getElementById("ev-error").innerText = "";

    // 저장된 캡처 이미지를 불러온다 (없으면 자리표시자 유지)
    const box = document.getElementById("ev-capture-img");
    box.querySelectorAll(".ev-capture-photo, .ev-capture-ph").forEach(el => el.remove());
    const img = document.createElement("img");
    img.className = "ev-capture-photo";
    img.alt = "낙상 캡처 이미지";
    img.style.cssText = "width:100%;height:100%;object-fit:cover;border-radius:4px;";
    img.onerror = () => {
        img.remove();
        const ph = document.createElement("span");
        ph.className = "ev-capture-ph";
        ph.innerHTML = "📷<br>저장된 캡처 이미지가 없습니다";
        box.prepend(ph);
    };
    img.src = `/api/fall-log/${id}/capture`;
    box.prepend(img);

    // 영상만으로는 같은 병실의 누가 넘어졌는지 알 수 없으므로 현장에서 확인해 지정한다
    const people = document.getElementById("ev-people");

    // 확정한 환자가 퇴원한 기록은 잠근다. 후보로 뜨는 건 '지금' 그 병실에 있는 환자라,
    // 그대로 두면 나중에 입원한 사람이 과거 낙상의 당사자로 기록된다 (서버도 409로 막는다)
    if (e.discharged) {
        people.innerHTML = "";
        const p = document.createElement("p");
        p.className = "a-hint";
        p.textContent = `${e.patient_name} 님으로 확정된 기록입니다. ` +
                        "해당 환자가 퇴원해 환자를 다시 지정할 수 없습니다.";
        people.appendChild(p);
        document.getElementById("ev-modal").classList.add("open");
        return;
    }

    WARDS = await fetchWards();
    const list = WARDS.filter(w => w.room === e.room_number);
    people.innerHTML = list.length
        ? list.map(p => h`
            <label class="ev-person${e.patient_id === p.id ? " picked" : ""}">
                <input type="radio" name="ev-fall-person" value="${p.id}"
                       ${e.patient_id === p.id ? "checked" : ""}
                       onchange="pickFallPatient(${p.id})">
                <span class="ev-person-name">${p.name}</span>
                <span class="ev-person-sub">${p.age}세 · ${p.sex} · ${p.risk}</span>
            </label>`).join("")
        : '<p class="a-hint">이 병실에 등록된 재원 환자가 없습니다.</p>';

    document.getElementById("ev-modal").classList.add("open");
}

// 라디오 선택 시 카드 강조만 갱신 (확정은 '대응 완료 처리'에서)
function pickFallPatient(patientId) {
    document.querySelectorAll("#ev-people .ev-person").forEach(el => {
        el.classList.toggle("picked", +el.querySelector("input").value === patientId);
    });
    document.getElementById("ev-error").innerText = "";
}

function closeEvCapture() {
    document.getElementById("ev-modal").classList.remove("open");
}

async function markEventDone() {
    if (evSelected === null) return;
    const err = document.getElementById("ev-error");

    // 낙상은 누가 넘어졌는지 지정해야 완료 처리할 수 있다
    const picked = document.querySelector('input[name="ev-fall-person"]:checked');
    if (!picked) {
        err.innerText = "현장에서 확인한 낙상 환자를 선택해 주세요.";
        return;
    }
    const patientId = +picked.value;
    const patientName = (WARDS.find(w => w.id === patientId) || {}).name;
    const memo = document.getElementById("ev-memo").value.trim();

    try {
        const res = await fetch(`/api/fall-log/${evSelected}/confirm`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ patient_id: patientId, memo })
        });
        const data = await res.json();
        if (!res.ok || !data.ok) { err.innerText = data.error || "처리 중 오류가 발생했습니다."; return; }
    } catch (e) {
        err.innerText = "서버와 통신할 수 없습니다.";
        return;
    }

    err.innerText = "";
    closeEvCapture();
    renderEvents();
    renderFalls();
    renderCharts();   // 오늘/이번 달 낙상 등 통계에도 바로 반영
    rcToast(patientName ? `${patientName} 님 낙상으로 확정했습니다` : "대응 완료로 처리했습니다");
}

// ----- 계정: 보호자 가입 승인 -----
// status: pending → approved(코드 발급) → registered(보호자가 코드로 가입 완료) / rejected
let PENDING = [];

// 코드 등록을 마친 보호자 계정 (렌더링 시 /api/guardian-accounts 에서 새로 받아온다)
let GUARDIANS = [];

async function fetchGuardians() {
    try {
        const res = await fetch("/api/guardian-accounts");
        const data = await res.json();
        return data.ok ? data.accounts.map(a => ({
            id: a.id,
            username: a.username,
            name: a.guardian || a.username,
            patient: a.name ? `${a.name} (${a.room_number}호)` : "연동 정보 없음",
            code: a.code || null,
            discharged: false
        })) : [];
    } catch (e) {
        return [];
    }
}

async function renderGuardians() {
    const tb = document.getElementById("acc-guardian-tbody");
    if (!tb) return;

    GUARDIANS = await fetchGuardians();

    tb.innerHTML = "";
    GUARDIANS.forEach((g, i) => {
        const tr = document.createElement("tr");
        tr.innerHTML =
            h`<td>${g.name}</td><td>${g.username}</td>` +
            h`<td>${g.patient}</td>` +
            h`<td>${g.code ? raw(h`<code class="g-code">${g.code}</code>`) : "-"}</td>` +
            `<td><span class="role-fixed role-user">USER</span></td>` +
            `<td class="row-actions"><button class="mini-btn mini-danger" onclick="dischargeByGuardian(${i})">계정 삭제</button></td>`;
        tb.appendChild(tr);
    });
}

// "홍길동 (101호)" → { name: "홍길동", room: "101" }
// 한 병실에 여러 환자가 있으므로 병실 번호만으로 찾으면 다른 환자를 건드리게 된다
function parsePatientRef(ref) {
    const m = String(ref).match(/^\s*(.+?)\s*\((\d{3,4})호\)\s*$/);
    return m ? { name: m[1].trim(), room: m[2] } : { name: "", room: "" };
}

// 보호자 계정을 삭제한다 (연동된 환자 정보도 함께 삭제됨)
function dischargeByGuardian(i) {
    const g = GUARDIANS[i];
    askConfirm({
        title: "보호자 계정 삭제",
        body: h`<b>${g.patient}</b> 보호자 <b>${g.name}</b> 님(${g.username}) 계정을 삭제합니다.<br>` +
              `환자 정보는 그대로 남고 연동만 해제되며, 이 계정으로 다시 로그인할 수 없습니다.`,
        okText: "계정 삭제",
        danger: true,
        onOk: async () => {
            try {
                const res = await fetch(`/api/guardian-accounts/${g.id}/delete`, { method: "POST" });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    rcToast(data.error || "삭제 중 오류가 발생했습니다");
                    return;
                }
            } catch (e) {
                rcToast("서버와 통신할 수 없습니다");
                return;
            }
            renderGuardians();
            rcToast(g.name + " 님 계정을 삭제했습니다");
        }
    });
}

// ===== 간호사 명부 (DB 연동) =====
const ROOM_LABEL = { all: "전체 (101~104호)", "101": "101호", "102": "102호", "103": "103호", "104": "104호" };

// 마지막으로 그린 간호사 목록. 삭제 버튼은 이름 대신 이 배열의 인덱스를 넘긴다
// (보호자·연동신청 목록과 같은 방식). 이름을 onclick 문자열에 끼워 넣으면
// 따옴표나 역슬래시가 든 이름에서 버튼이 깨진다.
let NURSES = [];

function toggleNurseForm() {
    const f = document.getElementById("nurse-form");
    if (!f) return;
    const opening = f.style.display === "none";
    f.style.display = opening ? "" : "none";
    if (opening) {
        ["nf-name", "nf-empno", "nf-phone"].forEach(id => {
            const el = document.getElementById(id); if (el) el.value = "";
        });
        document.getElementById("nf-room").value = "all";
        document.getElementById("nf-error").innerText = "";
        document.getElementById("nf-name").focus();
    }
}

async function renderNurses() {
    const tb = document.getElementById("acc-nurse-tbody");
    if (!tb) return;

    NURSES = [];
    try {
        const res = await fetch("/api/nurses");
        const data = await res.json();
        if (data.ok) NURSES = data.nurses;
    } catch (e) { /* 네트워크 오류 시 빈 목록 */ }

    tb.innerHTML = "";
    NURSES.forEach((n, i) => {
        const room = n.assigned_room || "all";
        const opts = ["all", "101", "102", "103", "104"].map(v =>
            `<option value="${v}"${v === room ? " selected" : ""}>${ROOM_LABEL[v]}</option>`
        ).join("");
        const tr = document.createElement("tr");
        tr.innerHTML =
            h`<td>${n.name || "-"}</td><td>${n.employee_no || "-"}</td><td>${n.phone || "-"}</td>` +
            `<td><select class="form-input form-input-sm" onchange="updateNurseRoom(${n.id}, this)">${opts}</select></td>` +
            `<td class="row-actions"><button class="mini-btn mini-danger" onclick="deleteNurse(${i})">삭제</button></td>`;
        tb.appendChild(tr);
    });
}

async function createNurse() {
    const err = document.getElementById("nf-error");
    const payload = {
        name: document.getElementById("nf-name").value.trim(),
        employee_no: document.getElementById("nf-empno").value.trim(),
        phone: document.getElementById("nf-phone").value.trim(),
        assigned_room: document.getElementById("nf-room").value,
    };
    err.innerText = "";
    if (!payload.name) { err.innerText = "이름은 필수입니다."; return; }
    if (payload.phone && !/^01[016789]-\d{3,4}-\d{4}$/.test(payload.phone)) {
        err.innerText = "전화번호를 010-0000-0000 형식으로 끝까지 입력해 주세요."; return;
    }

    try {
        const res = await fetch("/api/nurses", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok || !data.ok) { err.innerText = data.error || "간호사 추가 중 오류가 발생했습니다."; return; }
    } catch (e) { err.innerText = "서버와 통신할 수 없습니다."; return; }

    toggleNurseForm();
    renderNurses();
    rcToast(payload.name + " 간호사를 추가했습니다");
}

async function updateNurseRoom(id, sel) {
    const room = sel.value;
    try {
        const res = await fetch(`/api/nurses/${id}/room`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ assigned_room: room })
        });
        const data = await res.json();
        if (!res.ok || !data.ok) { rcToast(data.error || "저장 실패"); return; }
    } catch (e) { rcToast("서버와 통신할 수 없습니다"); return; }
    rcToast("담당 병실을 저장했습니다: " + (ROOM_LABEL[room] || room));
}

function deleteNurse(i) {
    const n = NURSES[i];
    if (!n) return;
    const id = n.id;
    askConfirm({
        title: "간호사 삭제",
        body: h`<b>${n.name}</b> 간호사를 삭제합니다.<br>삭제하면 이 계정으로 다시 로그인할 수 없습니다.`,
        okText: "삭제",
        danger: true,
        onOk: async () => {
            try {
                const res = await fetch(`/api/nurses/${id}/delete`, { method: "POST" });
                const data = await res.json();
                if (!res.ok || !data.ok) { rcToast(data.error || "삭제 중 오류가 발생했습니다"); return; }
            } catch (e) { rcToast("서버와 통신할 수 없습니다"); return; }
            renderNurses();
            rcToast(n.name + " 간호사를 삭제했습니다");
        }
    });
}

async function renderPending() {
    const tb = document.getElementById("acc-pending-tbody");
    if (!tb) return;

    try {
        const res = await fetch("/api/applications");
        const data = await res.json();
        PENDING = data.ok ? data.applications : [];
    } catch (e) {
        PENDING = [];
    }

    tb.innerHTML = "";
    PENDING.forEach((p, i) => {
        const tr = document.createElement("tr");
        const badge = p.status === "registered"
            ? '<span class="risk-chip risk-low">Registered</span>'
            : p.status === "approved"
                ? '<span class="risk-chip risk-mid">Approved</span>'
                : p.status === "rejected"
                    ? '<span class="risk-chip risk-high">Rejected</span>'
                    : '<span class="risk-chip risk-mid">Pending</span>';
        const actions = p.status === "pending"
            ? `<button class="mini-btn" onclick="approveGuardian(${i})">승인</button>
               <button class="mini-btn mini-danger" onclick="rejectGuardian(${i})">거절</button>`
            : p.code ? h`<code class="g-code">${p.code}</code>` : "-";
        tr.innerHTML =
            h`<td>${p.name}</td><td>${p.phone}</td><td>${p.patient}</td><td>${p.at}</td>` +
            h`<td>${raw(badge)}</td><td class="row-actions">${raw(actions)}</td>`;
        tb.appendChild(tr);
    });
    const n = PENDING.filter(p => p.status === "pending").length;
    const badge = document.getElementById("acc-pending-count");
    if (badge) badge.innerText = n;
}

function approveGuardian(i) {
    const p = PENDING[i];
    askConfirm({
        title: "보호자 연동 승인",
        body: h`<b>${p.name}</b> 님(${p.phone})을 <b>${p.patient}</b> 보호자로 승인합니다.<br>` +
              `승인하면 매핑 코드가 발급되어 등록된 연락처로 발송됩니다.`,
        okText: "승인",
        onOk: async () => {
            let data;
            try {
                const res = await fetch(`/api/applications/${p.id}/approve`, { method: "POST" });
                data = await res.json();
                if (!res.ok || !data.ok) {
                    rcToast(data.error || "매핑 코드를 만들지 못했습니다. 다시 시도해 주세요.");
                    return;
                }
            } catch (e) {
                rcToast("서버와 통신할 수 없습니다");
                return;
            }
            renderPending();
            rcToast(p.name + " 님 승인 완료 · 매핑 코드 " + data.code + " 발송 ");
        }
    });
}

function rejectGuardian(i) {
    const p = PENDING[i];
    askConfirm({
        title: "연동 신청 거절",
        body: h`<b>${p.name}</b> 님의 <b>${p.patient}</b> 연동 신청을 거절합니다.<br>` +
              `거절하면 매핑 코드가 발급되지 않아 계정을 만들 수 없습니다.`,
        okText: "거절",
        danger: true,
        onOk: async () => {
            try {
                const res = await fetch(`/api/applications/${p.id}/reject`, { method: "POST" });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    rcToast(data.error || "거절 처리 중 오류가 발생했습니다");
                    return;
                }
            } catch (e) {
                rcToast("서버와 통신할 수 없습니다");
                return;
            }
            renderPending();
            rcToast(p.name + " 님 신청을 거절했습니다");
        }
    });
}

// ===== 보호자 화면 =====
// 인사말에 로그인한 보호자 이름을 넣는다 ("박보호 · 보호자" → "박보호")
// 오늘의 순찰 기록 (2분 간격이라 건수가 많아 목록 안에서 스크롤된다)
async function renderPatrolLog(patient) {
    const ul = document.getElementById("g-patrol-log");
    const cnt = document.getElementById("g-patrol-count");
    if (!ul) return;

    let logs = [];
    if (patient) {
        try {
            const res = await fetch("/api/patrol-log");
            const data = await res.json();
            if (data.ok) logs = data.logs;   // 최신순(DESC)
        } catch (e) { /* 네트워크 오류 시 빈 목록으로 처리 */ }
    }
    if (cnt) cnt.innerText = logs.length;

    // "오늘 순찰"/"최근 30일 순찰" 개수는 이 목록(최대 20건)이 아니라 loadGuardianStats()의
    // 정확한 COUNT 집계값을 쓴다 — 여기서는 "가장 최근 순찰 시각"만 채운다.
    // 서버가 "YYYY-MM-DD HH:MM:SS" 형식으로 내려준다
    const fmtTime = dt => dt ? String(dt).slice(11, 16) : "";   // HH:MM (타임라인용)
    const fmtDateTime = dt => {                                  // 2026년 8월 7일 16:20 (최근 순찰용)
        if (!dt) return "";
        const m = String(dt).match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
        return m ? `${m[1]}년 ${+m[2]}월 ${+m[3]}일 ${m[4]}:${m[5]}` : String(dt).slice(0, 16);
    };
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v; };
    // 해당 병실을 순찰한 가장 최근 시각을 날짜+시간으로 표시 (순찰 기록이 쌓이면 자동 갱신)
    const lastPatrol = logs.length ? fmtDateTime(logs[0].patrolled_at) : "기록 없음";
    set("g-live-last", lastPatrol);
    set("g-live-lasttile", lastPatrol);

    if (!logs.length) {
        ul.innerHTML = '<li class="g-log-empty">아직 오늘 순찰 기록이 없습니다.</li>';
        return;
    }

    ul.innerHTML = logs.map(l =>
        `<li><span class="tl-time">${fmtTime(l.patrolled_at)}</span><span>정기 순찰 완료 · <span class="g-ok">이상 없음</span></span></li>`
    ).join("");
}

// 간호사가 환자를 특정한 낙상 기록을 보호자 화면(홈·통계)에 반영한다
async function renderGuardianFalls(patient) {
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v; };

    let falls = [];
    if (patient) {
        try {
            const res = await fetch("/api/fall-log");
            const data = await res.json();
            falls = data.ok ? data.logs : [];   // 서버가 이미 본인 환자 + 확정된 것만 내려줌
        } catch (e) { /* 네트워크 오류 시 빈 목록으로 처리 */ }
    }

    // falls는 최신순(DESC) — 내 병실 탭의 환자 프로필 "최근 낙상 기록"도 여기서 같이 채운다
    set("g-pt-lastfall", falls.length ? falls[0].detected_at : "낙상 이력 없음");

    const today = new Date();
    const ymd = `${today.getFullYear()}-${pad2(today.getMonth() + 1)}-${pad2(today.getDate())}`;
    const todayFalls = falls.filter(f => f.detected_at.startsWith(ymd));

    // 최근 30일
    const limit = new Date(today.getTime() - 30 * 86400000);
    const recent = falls.filter(f => new Date(f.detected_at.replace(" ", "T")) >= limit);

    // Home · 오늘의 알림 카드
    const tile = document.getElementById("g-today-events");
    if (tile) {
        tile.innerHTML = todayFalls.length + '<span class="stat-tile-unit">건</span>';
        tile.style.color = todayFalls.length ? "#e5484d" : "";
    }
    set("g-today-events-sub", todayFalls.length ? "낙상 알림 확인 필요" : "낙상 감지 없음");

    // Home · 낙상 알림 기록 (있을 때만 노출)
    const panel = document.getElementById("g-fall-panel");
    const list = document.getElementById("g-fall-list");
    if (panel && list) {
        panel.style.display = falls.length ? "" : "none";
        list.innerHTML = falls.map(f => {
            const memo = f.memo ? h` · <span class="g-fall-memo">${f.memo}</span>` : "";
            return h`<li><span class="tl-time">${f.detected_at.slice(5, 16)}</span>` +
                   h`<span><span class="g-danger">낙상 감지</span> · 확인 완료${raw(memo)}</span></li>`;
        }).join("");
    }

    // 통계
    const fallsNum = document.getElementById("g-stat-falls");
    if (fallsNum) {
        fallsNum.innerHTML = recent.length + '<span class="stat-tile-unit">건</span>';
        fallsNum.style.color = recent.length ? "#e5484d" : "#1a9d43";
    }
    set("g-stat-falls-sub", recent.length ? "간호사 확인 완료" : "안전 유지 중");
    set("g-stats-chip", `최근 30일 낙상 ${recent.length}건`);
    set("g-report-sub", `같은 기간 낙상 발생 ${recent.length}건`);

    const chip = document.getElementById("g-stats-chip");
    if (chip) {
        chip.classList.toggle("g-chip-ok", recent.length === 0);
        chip.classList.toggle("g-chip-warn", recent.length > 0);
    }
}

// 로그인한 보호자 계정 → 연동된 환자를 찾아 보호자 화면 전체를 채운다.
// (하드코딩 없이 DB 조회 결과만 사용 — 나중에 API 로 바꿔도 이 함수는 그대로)
let currentGuardianRoom = null;   // 실시간 병실 화면에서 "내 병실" 여부 판단용

async function renderGuardian() {
    const set = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.innerText = text;
    };

    const username = currentUser();
    let patient = null;
    if (username) {
        try {
            const res = await fetch("/api/my-patient");
            const data = await res.json();
            if (data.ok) patient = data.patient;
        } catch (e) { /* 네트워크 오류 시 연동 정보 없음으로 처리 */ }
    }

    set("g-greet-name", ((patient && patient.guardian) || username || "보호자").trim());

    if (!patient) {
        // 연동 정보를 못 찾은 경우 (승인 전이거나 데이터 불일치)
        ["g-hero-room", "g-hero-patient", "g-tile-room", "g-pt-name", "g-pt-age", "g-pt-dx",
         "g-pt-lastfall", "g-room-no", "g-room-type", "g-live-room", "g-live-room2",
         "g-live-type", "g-live-patient", "g-stats-patient",
         "g-link-patient", "g-link-code"].forEach(id => set(id, "연동 정보 없음"));
        const nt = document.getElementById("g-nurse-tbody");
        if (nt) nt.innerHTML = `<tr><td colspan="3">연동 정보 없음</td></tr>`;
        currentGuardianRoom = null;
        renderPatrolLog(null);
        renderGuardianFalls(null);
        loadGuardianNoti();
        return;
    }

    currentGuardianRoom = patient.room_number;
    const room = patient.room_number + "호";
    const sexText = patient.sex || "-";

    // Home
    set("g-hero-room", room);
    set("g-hero-patient", patient.name);
    set("g-tile-room", room);

    // 내 병실 · 환자 프로필
    set("g-pt-name", patient.name);
    set("g-pt-age", `${patient.age}세 · ${sexText}`);
    set("g-pt-dx", patient.disease || "-");
    set("g-pt-lastfall", "-");

    const riskEl = document.getElementById("g-pt-risk");
    if (riskEl) {
        riskEl.innerHTML = patient.risk_level
            ? h`<span class="risk-chip risk-${RISK_CLASS[patient.risk_level] || "idle"}">${patient.risk_level}</span>`
            : "-";
    }
    const noteEl = document.getElementById("g-pt-note");
    if (noteEl) {
        noteEl.style.display = (patient.risk_level === "높음" || patient.risk_level === "매우 높음") ? "" : "none";
    }

    // 내 병실 · 병실 상세
    set("g-room-no", room);
    set("g-room-type", roomTypeOf(patient.room_number));

    // 담당 간호사: 표 형식으로 한 명당 한 행 (여러 명이어도 아래로 늘어남)
    const nurses = patient.nurses || [];
    const nurseTbody = document.getElementById("g-nurse-tbody");
    if (nurseTbody) {
        nurseTbody.innerHTML = nurses.length
            ? nurses.map(n =>
                h`<tr><td>${n.name || "-"}</td><td>${n.employee_no || "-"}</td><td>${n.phone || "-"}</td></tr>`
              ).join("")
            : `<tr><td colspan="3">배정된 담당 간호사가 없습니다</td></tr>`;
    }

    // 실시간 병실
    set("g-live-room", room);
    set("g-live-room2", room);
    set("g-live-type", roomTypeOf(patient.room_number));
    set("g-live-patient", patient.name + " 님");
    set("g-map-room", room);
    set("g-map-patient", "내 병실 · " + patient.name + " 님");
    set("g-map-ward", "-");

    // 통계
    set("g-stats-patient", `${patient.name} 님(${room})`);

    // 내 정보
    set("g-me-name", patient.guardian || username);
    set("g-me-id", username);
    set("g-me-phone", patient.phone || "-");
    set("g-link-patient", `${patient.name} (${room})`);
    set("g-link-code", patient.mapping_code || "-");

    await renderPatrolLog(patient);
    renderGuardianFalls(patient);
    loadGuardianNoti();
    refreshGuardianFallState();   // 확정 낙상 기준 환자 상태색 반영
}

const G_NOTI_KEY = "dabom-guardian-noti";

function loadGuardianNoti() {
    const fall = document.getElementById("g-noti-fall");
    if (!fall) return;
    let saved;
    try { saved = JSON.parse(localStorage.getItem(G_NOTI_KEY)); } catch (e) { saved = null; }
    if (!saved) return;                       // 저장값이 없으면 HTML 기본값(낙상 ON) 유지
    fall.checked = !!saved.fall;
}

function saveGuardianNoti() {
    const fall = document.getElementById("g-noti-fall").checked;
    localStorage.setItem(G_NOTI_KEY, JSON.stringify({ fall }));
    rcToast("알림 설정이 저장되었습니다");
}

// 실시간 병실(보호자): 조회 전용으로 로봇 상태·위치만 반영한다
function syncGuardianLive(data) {
    const state = document.getElementById("g-robot-state");
    if (!state) return;
    const mine = currentGuardianRoom != null && String(data.current_room) === String(currentGuardianRoom);
    state.innerText = data.robot_status || "-";
    document.getElementById("g-robot-loc").innerText =
        mine ? `${currentGuardianRoom}호 순찰 중` : "복도 이동 중";
    document.getElementById("g-camera").innerText = data.camera || "-";

    // 낙상 표시: 관리자가 확정한 낙상 상태(guardianFallState)가 우선한다.
    // 확정 낙상이 없을 때만 로봇의 실시간 감지 신호를 반영한다.
    const fall = document.getElementById("g-fall-state");
    if (guardianFallState === "fall") {
        fall.innerText = "낙상 감지";
        fall.classList.remove("g-ok"); fall.classList.add("g-danger");
    } else if (guardianFallState === "warning") {
        fall.innerText = "낙상 있었음";
        fall.classList.remove("g-ok", "g-danger");
        fall.style.color = "#e08c1a";
    } else {
        fall.style.color = "";
        const detected = mine && data.fall_status && data.fall_status.includes("감지");
        fall.innerText = detected ? "낙상 감지" : "정상";
        fall.classList.toggle("g-ok", !detected);
        fall.classList.toggle("g-danger", detected);
    }

    // 병실 구조도 · 로봇 실시간 위치 (열람 전용)
    if (typeof data.robot_x === "number" && typeof data.robot_y === "number") {
        placeRobotIconByMap("g-live-robot", data.robot_x, data.robot_y);
        gLiveSetMapLive(true);
    } else {
        gLiveSetMapLive(false);
    }
    drawMapPath("g-live-path", data.path);
}

// ===== 보호자 환자 상태 (관리자가 확정한 낙상 기준: 낙상 10분 → 주의 → 다음날 정상) =====
let guardianFallState = "normal";

const FALL_STATE_UI = {
    normal:  { color: "#1a9d43", status: "안정", detect: "감지 없음",   sub: "이상 징후 없음",       chip: "현재 안정",         heroChip: "#4ade80", heroBorder: "rgba(74, 222, 128, 0.55)" },
    fall:    { color: "#e5484d", status: "낙상", detect: "감지됨",       sub: "낙상 발생 · 대응 중",   chip: "낙상 감지",         heroChip: "#ff9aa0", heroBorder: "rgba(255, 154, 160, 0.6)" },
    warning: { color: "#e08c1a", status: "주의", detect: "낙상 있었음",  sub: "오늘 낙상 발생 이력",   chip: "주의 · 낙상 있었음", heroChip: "#ffcf82", heroBorder: "rgba(255, 207, 130, 0.6)" },
};

function applyGuardianFallState(stateKey, lastDetectedAt) {
    const ui = FALL_STATE_UI[stateKey] || FALL_STATE_UI.normal;
    guardianFallState = stateKey in FALL_STATE_UI ? stateKey : "normal";
    const setHTML = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };

    // 홈 히어로 칩
    const chip = document.getElementById("g-hero-chip");
    if (chip) {
        chip.innerHTML = `<i></i>${ui.chip}`;
        chip.style.color = ui.heroChip;          // 글자 + 점(currentColor)
        chip.style.borderColor = ui.heroBorder;  // 테두리
    }
    // 홈 요약카드 · 현재 환자 상태
    const num = document.getElementById("g-status-num");
    if (num) { num.innerText = ui.status; num.style.color = ui.color; }
    setHTML("g-status-sub", `<span class="g-dot" style="background:${ui.color};box-shadow:0 0 0 3px ${ui.color}28"></span>${ui.sub}`);
    // 내 병실 · 현재 환자 상태 / 최근 감지
    setHTML("g-pt-status", `<span style="color:${ui.color};font-weight:700;">${ui.status}</span>`);
    // "최근 감지"는 상태 문구가 아니라, 그 병실에서 확정된 낙상 중 가장 최근 실제 시각을 보여준다
    // (내 담당 환자만이 아니라 그 병실 누구든 해당 — "최근 낙상 기록"과는 다른 기준)
    setHTML("g-pt-lastdetect", lastDetectedAt || "없음");
    // 실시간 병실 · 환자 상태 박스 색(테두리·배경·점) + 서브문구
    const box = document.getElementById("g-state-box");
    if (box) {
        box.classList.remove("state-fall", "state-warning");
        if (guardianFallState === "fall") box.classList.add("state-fall");
        else if (guardianFallState === "warning") box.classList.add("state-warning");
    }
    const subEl = document.getElementById("g-fall-substate");
    if (subEl) subEl.innerText = ui.detect === "감지 없음" ? "낙상 감지 없음" : ui.detect;
    // 위 큰 문구(정상/낙상 감지/낙상 있었음)도 여기서 바로 바꿔 서브문구·색과 시점을 맞춘다.
    // (syncGuardianLive가 1초마다 유지하지만, 전환 순간의 지연을 없애려고 즉시 반영)
    const fs = document.getElementById("g-fall-state");
    if (fs) {
        fs.style.color = "";
        fs.classList.remove("g-ok", "g-danger");
        if (guardianFallState === "fall") { fs.innerText = "낙상 감지"; fs.classList.add("g-danger"); }
        else if (guardianFallState === "warning") { fs.innerText = "낙상 있었음"; fs.style.color = "#e08c1a"; }
        else { fs.innerText = "정상"; fs.classList.add("g-ok"); }
    }
}

async function refreshGuardianFallState() {
    if (currentRole() !== "guardian") return;   // 보호자일 때만 조회
    try {
        const res = await fetch("/api/my-fall-state");
        const data = await res.json();
        if (data.ok) applyGuardianFallState(data.state, data.last_detected_at);
    } catch (e) { /* 네트워크 오류 시 이전 상태 유지 */ }
}

async function doLogin() {
    const id = document.getElementById("login-id").value.trim();
    const pw = document.getElementById("login-pw").value;
    const auto = document.getElementById("login-auto").checked;

    let data;
    try {
        const res = await fetch("/api/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: id, password: pw, remember: auto, role: loginRole })
        });
        data = await res.json();
        if (!res.ok || !data.ok) {
            document.getElementById("login-error").innerText = data.error || "아이디 또는 비밀번호가 올바르지 않습니다.";
            return;
        }
    } catch (e) {
        document.getElementById("login-error").innerText = "서버와 통신할 수 없습니다.";
        return;
    }

    // 자동 로그인 → localStorage(영구), 미체크 → sessionStorage
    const store = auto ? localStorage : sessionStorage;
    store.setItem(AUTH_KEY, "1");
    store.setItem(ROLE_KEY, toAppRole(data.role));   // DB 표기(admin/user) → 화면 표기(admin/guardian)
    store.setItem(NAME_KEY, data.username);
    store.setItem(USER_KEY, data.username);
    document.getElementById("login-error").innerText = "";
    document.getElementById("login-pw").value = "";
    document.getElementById("login-id").value = "";
    closeLogin();
    renderNav();
    reloadVideo();   // 로그인 전 401로 끊긴 영상을 다시 붙인다
    if (toAppRole(data.role) === "admin") {
        renderPending();
        renderGuardians();
        renderNurses();
        renderPatients();
        renderEvents();
        renderFalls();
    }
    renderCharts();   // 관리자/보호자 통계 차트 둘 다 이 함수 하나가 처리한다
    await renderGuardian();
    goHome();
}

async function logout() {
    try {
        await fetch("/api/logout", { method: "POST" });
    } catch (e) { /* 서버 통신 실패해도 로컬 상태는 정리 */ }
    [localStorage, sessionStorage].forEach(s => {
        s.removeItem(AUTH_KEY); s.removeItem(ROLE_KEY); s.removeItem(NAME_KEY); s.removeItem(USER_KEY);
    });
    stopVideo();   // 열려 있던 영상 스트림도 함께 끊는다
    renderNav();
    goHome();
}

// Enter 키로 로그인
["login-id", "login-pw"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });
});

// 비밀번호 변경 (보호자 · 내 정보 탭)
async function changePassword() {
    const current = document.getElementById("pw-current").value;
    const next = document.getElementById("pw-new").value;
    const next2 = document.getElementById("pw-new2").value;
    const err = document.getElementById("pw-error");
    err.innerText = "";

    if (!current || !next || !next2) { err.innerText = "모든 항목을 입력해 주세요."; return; }
    if (next.length < 4) { err.innerText = "새 비밀번호는 4자 이상이어야 합니다."; return; }
    if (next !== next2) { err.innerText = "새 비밀번호가 서로 일치하지 않습니다."; return; }
    if (next === current) { err.innerText = "현재 비밀번호와 다른 비밀번호를 입력해 주세요."; return; }

    try {
        const res = await fetch("/api/change-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ current_password: current, new_password: next })
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            err.innerText = data.error || "비밀번호를 변경할 수 없습니다.";
            return;
        }
    } catch (e) {
        err.innerText = "서버와 통신할 수 없습니다.";
        return;
    }

    document.getElementById("pw-current").value = "";
    document.getElementById("pw-new").value = "";
    document.getElementById("pw-new2").value = "";
    rcToast("비밀번호가 변경되었습니다.");
}

let currentVideoMode = "raw";
let lastYoloSignal = false;
let lastSeenAlertId = null;  // * 팝업 부분 수정 *
let lastLogHtml = "";        // 이벤트 로그: 바뀐 경우에만 다시 그리기 위한 비교용
let lastEventsData = [];     // 이벤트 로그 탭 전환 시 폴링을 안 기다리고 바로 다시 그리기 위한 캐시
let lastFallLogData = [];    // "낙상 현황" 탭 전용 — fall_log(DB)에서 가져온 값, 서버 재시작해도 안 사라짐
let monLogTab = "robot";     // "robot"(로봇 상태) / "fall"(낙상 현황)

function setMonLogTab(tab) {
    monLogTab = tab;
    document.querySelectorAll(".mon-log-tab").forEach(b => {
        b.classList.toggle("active", b.dataset.logTab === tab);
    });
    lastLogHtml = "";   // 탭이 바뀌면 내용이 같아 보여도 무조건 다시 그린다
    if (tab === "fall") { loadFallLogTab(); } else { renderEventLog(); }
}

// "낙상 현황" 탭 데이터는 메모리(state.events)가 아니라 DB(fall_log)에서 매번 새로 받아온다
async function loadFallLogTab() {
    try {
        const res = await fetch("/api/fall-log");
        const data = await res.json();
        lastFallLogData = data.ok ? data.logs : [];
    } catch (e) {
        lastFallLogData = [];
    }
    renderEventLog();
}

function renderEventLog() {
    const log = document.getElementById("event-log");
    if (!log) return;

    let html, count;
    if (monLogTab === "fall") {
        count = lastFallLogData.length;
        html = lastFallLogData.map(f => {
            const time = String(f.detected_at).slice(5, 16);   // "MM-DD HH:MM"
            const who = f.patient_name ? h` · ${f.patient_name} 님` : "";
            return h`<div class="event-item fall"><span>${time}</span><p>병실 ${f.room_number} 낙상 감지${raw(who)}</p></div>`;
        }).join("") || '<div class="event-empty">기록된 낙상이 없습니다.</div>';
    } else {
        const filtered = lastEventsData.filter(e => !e.text.includes("낙상 환자 발견"));
        count = filtered.length;
        html = filtered.map(e =>
            h`<div class="event-item"><span>${e.time}</span><p>${e.text}</p></div>`
        ).join("") || '<div class="event-empty">기록된 이벤트가 없습니다.</div>';
    }

    if (html !== lastLogHtml) {
        const keep = log.scrollTop;
        log.innerHTML = html;
        lastLogHtml = html;
        log.scrollTop = keep;
    }

    const logCount = document.getElementById("mon-log-count");
    if (logCount) logCount.innerText = count;
}

function showFallAlert(room) {  // * 팝업 부분 수정 *
    document.getElementById("fall-alert-room").innerText = "병실 " + room;  // * 팝업 부분 수정 *
    document.getElementById("fall-alert-overlay").style.display = "flex";  // * 팝업 부분 수정 *
}  // * 팝업 부분 수정 *

function closeFallAlert() {  // * 팝업 부분 수정 *
    document.getElementById("fall-alert-overlay").style.display = "none";  // * 팝업 부분 수정 *
}  // * 팝업 부분 수정 *

// 팝업 확인 → 닫고 '병실 모니터링' 탭으로 이동
function goFallMonitoring() {
    closeFallAlert();
    showTab("monitoring");
}

// 영상 라우트가 @login_required라, 로그인 전에는 <img>가 401을 받고 오류 상태로 굳는다.
// 로그인 직후 여기서 다시 붙여야 모니터링 탭에 영상이 나온다.
// 같은 URL을 그대로 넣으면 브라우저가 재요청을 안 할 수 있어 쿼리를 붙인다.
function reloadVideo() {
    const img = document.getElementById("video-img");
    if (!img) { return; }
    const base = currentVideoMode === "yolo" ? "/video_feed_yolo" : "/video_feed";
    img.style.visibility = "visible";
    if (img.parentElement) { img.parentElement.classList.remove("no-signal"); }
    img.src = base + "?t=" + Date.now();
}

// 로그아웃하면 열려 있던 스트림도 끊는다. 안 그러면 이미 연결된 영상은 계속 흐른다
function stopVideo() {
    const img = document.getElementById("video-img");
    if (!img) { return; }
    img.removeAttribute("src");
    img.style.visibility = "hidden";
}

function switchVideo(mode) {
    currentVideoMode = mode;
    const img = document.getElementById("video-img");
    const btnRaw = document.getElementById("btn-raw");
    const btnYolo = document.getElementById("btn-yolo");

    if (mode === "yolo") {
        img.src = "/video_feed_yolo";
        img.style.visibility = lastYoloSignal ? "visible" : "hidden";
        btnYolo.classList.add("active");
        btnRaw.classList.remove("active");
    } else {
        img.style.visibility = "visible";
        img.src = "/video_feed";
        btnRaw.classList.add("active");
        btnYolo.classList.remove("active");
    }
}

// 배터리 표시(숫자 + 막대) 한 쌍을 갱신한다.
// 모니터링 탭과 Robot Control이 같은 값을 서로 다른 element에 그려서 함수로 뺐다.
// 30% 이하 주의(low), 15% 이하 위험(crit) — 색은 style.css가 정한다
function renderBattery(textId, barId, battery) {
    const text = document.getElementById(textId);
    if (text) { text.innerText = battery; }

    const bar = document.getElementById(barId);
    if (!bar) { return; }

    const pct = parseInt(String(battery).replace(/[^0-9]/g, ""), 10);
    if (isNaN(pct)) {
        // "배터리 대기" 처럼 숫자가 없으면 막대를 비운다
        bar.style.width = "0%";
        bar.className = "";
        return;
    }

    bar.style.width = pct + "%";
    bar.className = pct <= 15 ? "crit" : pct <= 30 ? "low" : "";
}

// ===== 병실별 낙상 · 순찰 집계 =====
// 병실 카드에 "순찰 n회 · 낙상 n건"을 적고, 낙상이 잦은 병실일수록 붉게 칠한다.
// 절대 건수가 아니라 '가장 많은 병실 대비 비율'로 칠하는 이유: 운영 초기에는
// 전체 건수가 적어서 고정 기준(예: 5건 이상 빨강)으로는 아무 색도 안 나온다.
async function renderRoomTally() {
    try {
        const res = await fetch("/api/rooms/summary");
        const data = await res.json();
        if (!data.ok) { return; }

        const maxFalls = Math.max(0, ...data.rooms.map(r => r.falls));

        data.rooms.forEach(r => {
            const cell = document.getElementById("room-" + r.room);
            if (!cell) { return; }

            cell.classList.remove("fall-low", "fall-mid", "fall-high");
            if (!r.falls) { return; }   // 0건이면 색 없음

            const ratio = r.falls / maxFalls;
            cell.classList.add(
                ratio > 0.66 ? "fall-high" : ratio > 0.33 ? "fall-mid" : "fall-low");
        });
    } catch (e) {
        console.error(e);
    }
}

async function updateStatus() {
    try {
        const res = await fetch("/api/status");
        if (!res.ok) return;   // 로그인 안 된 상태(401)에서는 fall_alert_id가 없어 오탐 팝업이 뜨므로 무시
        const data = await res.json();

        document.querySelectorAll(".room").forEach(room => {
            room.classList.remove("active-room");
        });

        // 로봇이 있는 병실은 실제 좌표로 판정한다. ArUco 마커(current_room)는 마커를
        // 다시 볼 때까지 값이 안 바뀌어서, 병실을 나온 뒤에도 그 병실에 남아 보였다
        const robotRoom = robotRoomNumber(data);

        const room = document.getElementById("room-" + robotRoom);
        const robotMarker = document.getElementById("robot-marker");
        if (room) {
            room.classList.add("active-room");
            room.appendChild(robotMarker);
            robotMarker.style.display = "block";
        } else {
            robotMarker.style.display = "none";   // 복도 등 병실 밖
        }

        // 로봇 제어 지도의 실시간 위치. 폴링 간격(1초)에 맞춰 CSS가 사이를 이어 그린다
        if (typeof data.robot_x === "number" && typeof data.robot_y === "number") {
            placeRobotIconByMap("rm-robot", data.robot_x, data.robot_y);
            rcSetMapLive(true);
            // 현재 위치는 마커가 아니라 실제 좌표가 어느 구역에 드는지로 정한다
            const zoneKey = rcZoneKeyByMap(data.robot_x, data.robot_y);
            rcSetCurrent(zoneKey === undefined ? "위치 확인 중" : rcZoneLabel(zoneKey));
        } else {
            rcSetMapLive(false);   // AMCL 없음 → 마지막 위치를 흐리게
            rcSetCurrent("위치 확인 중");
        }

        drawMapPath("rc-path", data.path);          // Nav2 경로
        rcSyncPauseButton(!!data.robot_paused);     // 새로고침해도 버튼 라벨이 맞도록
        // 일시정지 중에는 로봇이 안 움직여 "대기 중"으로 잡히므로 구분해서 보여준다
        rcSetState(data.robot_paused ? "일시정지" : data.robot_status);

        lastYoloSignal = data.yolo_signal;
        if (currentVideoMode === "yolo") {
            document.getElementById("video-img").style.visibility = data.yolo_signal ? "visible" : "hidden";
        }

        if (lastSeenAlertId === null) {  // * 팝업 부분 수정 *
            lastSeenAlertId = data.fall_alert_id;  // * 팝업 부분 수정 *
        } else if (data.fall_alert_id !== lastSeenAlertId) {  // * 팝업 부분 수정 *
            lastSeenAlertId = data.fall_alert_id;  // * 팝업 부분 수정 *
            renderRoomTally();   // 낙상이 새로 났으니 병실 색·건수를 30초 기다리지 말고 갱신
            // 관리자 계정에만 낙상 팝업을 띄운다
            if (currentRole() === "admin") {
                showFallAlert(data.current_room);  // * 팝업 부분 수정 *
            }
        }  // * 팝업 부분 수정 *

        document.getElementById("robot-status").innerText = data.robot_status;
        // Home 탭 "Robot 상태" 타일: 텍스트뿐 아니라 상태별로 색도 맞춘다
        const homeRobotState = document.getElementById("stat-robot-state");
        if (homeRobotState) {
            const stateText = data.robot_paused ? "일시정지" : data.robot_status;
            homeRobotState.innerText = stateText;
            homeRobotState.style.color =
                data.robot_paused ? "#f59e0b" :
                data.robot_status === "이동 중" ? "#1a9d43" : "#64748b";
        }
        document.getElementById("fall-status").innerText = data.fall_status;
        document.getElementById("camera").innerText = data.camera;
        document.getElementById("network").innerText = data.network;
        const hasPos = typeof data.robot_x === "number" && typeof data.robot_y === "number";
        const locText = robotRoom ? "병실 " + robotRoom : (hasPos ? "복도" : "위치 확인 중");
        document.getElementById("current-room-text").innerText = locText;

        // 모니터링 콘솔: 영상 위 병실 표기 · 배터리 막대 · 낙상 강조
        const stageRoom = document.getElementById("mon-stage-room");
        if (stageRoom) stageRoom.innerText = locText;

        // 배터리 · 속도 · 연결 상태 (모니터링 탭과 Robot Control 양쪽)
        renderBattery("battery", "battery-bar", data.battery);
        renderBattery("rc-battery", "rc-battery-bar", data.battery);

        const speedEl = document.getElementById("rc-speed");
        if (speedEl) {
            speedEl.innerText = typeof data.speed === "number"
                ? data.speed.toFixed(2) + " m/s" : "-";
        }

        // 로봇에서 데이터가 들어오는지 여부. 하드코딩된 "연결됨"을 대체한다
        const connected = data.network === "네트워크 연결";
        ["mon-conn-text", "rc-conn", "rc-conn-chip-text"].forEach(id => {
            const el = document.getElementById(id);
            if (el) { el.innerText = data.network; }
        });

        // 끊기면 칩 색도 경고로 (초록 그대로면 끊긴 걸 못 알아챈다)
        const connChip = document.getElementById("rc-conn-chip");
        if (connChip) {
            connChip.classList.toggle("a-chip-ok", connected);
            connChip.classList.toggle("a-chip-warn", !connected);
        }

        const fallBox = document.getElementById("mon-fall-box");
        if (fallBox) {
            fallBox.classList.toggle("alert", !!data.fall_status && data.fall_status.includes("감지"));
        }

        // 홈 탭 요약 지표 동기화 (기존 status 데이터 재사용)
        const statRoom = document.getElementById("stat-current-room");
        if (statRoom) statRoom.innerText = room ? "병실 " + robotRoom : "확인 중";
        const statBattery = document.getElementById("stat-battery");
        if (statBattery) statBattery.innerText = data.battery;

        syncGuardianLive(data);   // 보호자 실시간 병실 (조회 전용)

        // 로그는 1초마다 다시 그려지므로, 내용이 바뀐 경우에만 갱신하고
        // 관리자가 위로 올려 읽던 스크롤 위치는 그대로 유지한다
        lastEventsData = data.events;
        renderEventLog();

        const ahomeEvents = document.getElementById("ahome-events");
        if (ahomeEvents) {
            ahomeEvents.innerHTML = data.events.slice(0, 20).map(e => {
                const isFall = e.text.includes("낙상 환자 발견");
                const tag = isFall ? '<span class="risk-chip risk-high">낙상</span> ' : "";
                return h`<li><span class="tl-time">${e.time}</span><span>${raw(tag)}${e.text}</span></li>`;
            }).join("") || '<li class="g-log-empty">기록된 이벤트가 없습니다.</li>';
        }
    } catch (err) {
        console.error(err);
    }
}

setInterval(updateStatus, 1000);
updateStatus();

// ===== 병실 낙상 위험도 =====
// 점수 체계는 my_patrol/priority_patrol.py 와 같은 값이다. 순찰 노드가 이 점수로
// 순찰 순서를 정하므로, 두 곳이 어긋나면 화면과 로봇 행동이 달라진다.
// ※ 환자 원본은 갈라져 있다 — 순찰 노드는 patients.yaml, 이 화면은 DB(/api/patients).
const FALL_RISK_SCORES = { "매우 높음": 50, "높음": 35, "보통": 20, "낮음": 5 };
const AGE_SCORE_BANDS  = [[80, 30], [70, 25], [60, 20], [50, 10]];
const DISEASE_SCORES   = [["치매", 25], ["뇌경색", 25], ["파킨슨", 25],
                          ["대퇴골", 20], ["골절", 20], ["심부전", 15]];
const BASE_SCORE = 5;   // 어느 구간에도 안 걸리는 나이·질환의 기본점

// 한 사람이 받을 수 있는 최대 점수 = 막대의 100% 기준선.
// 축을 관측 최댓값이 아니라 이 값에 고정해야 병실끼리·시점끼리 길이가 비교된다
const MAX_PATIENT_SCORE = Math.max(...Object.values(FALL_RISK_SCORES))
                        + AGE_SCORE_BANDS[0][1]
                        + Math.max(...DISEASE_SCORES.map(d => d[1]));

function patientRiskScore(p) {
    const fall = FALL_RISK_SCORES[p.risk_level] || 0;
    const band = AGE_SCORE_BANDS.find(([floor]) => p.age != null && p.age >= floor);
    const hit  = DISEASE_SCORES.find(([key]) => String(p.disease || "").includes(key));
    const ageScore = band ? band[1] : BASE_SCORE;
    const disScore = hit ? hit[1] : BASE_SCORE;
    return { fallScore: fall, ageScore, disScore, total: fall + ageScore + disScore };
}

// 막대 한 줄(호실 · 막대 · 숫자)을 만들어 돌려준다.
// 위험도 차트와 오늘 순찰 차트가 같은 생김새라 조립을 한 곳에 모았다.
// 값은 관리자가 입력한 내용이 섞일 수 있어 전부 textContent로만 넣는다.
function buildRiskBarRow(roomLabel, value, pct) {
    const row = document.createElement("div");
    row.className = "riskbar";
    row.setAttribute("role", "listitem");

    const label = document.createElement("span");
    label.className = "riskbar-room";
    label.textContent = roomLabel;

    const track = document.createElement("span");
    track.className = "riskbar-track";
    const fill = document.createElement("span");
    fill.className = "riskbar-fill";
    fill.style.width = pct + "%";
    track.appendChild(fill);

    const valueEl = document.createElement("span");
    valueEl.className = "riskbar-value";
    valueEl.textContent = value;

    row.append(label, track, valueEl);
    return row;
}

async function renderRoomRisk() {
    const box = document.getElementById("mon-risk-chart");
    if (!box) { return; }

    const maxEl = document.getElementById("mon-risk-max");
    if (maxEl) { maxEl.innerText = MAX_PATIENT_SCORE; }

    let patients;
    try {
        const res = await fetch("/api/patients");
        const data = await res.json();
        if (!data.ok) { throw new Error(data.error || "실패"); }
        patients = data.patients;
    } catch (e) {
        box.innerHTML = '<p class="riskbars-empty">위험도를 불러오지 못했습니다.</p>';
        return;
    }

    // 병실 점수 = 그 병실 환자 중 개인 점수 '최댓값'.
    // 합계가 아닌 이유: 순찰 우선순위는 가장 위험한 환자 한 명이 정한다.
    // 저위험 환자가 여럿이라고 우선순위가 올라가면 안 된다.
    const byRoom = {};
    patients.forEach(p => {
        if (!p.room_number) { return; }
        (byRoom[p.room_number] = byRoom[p.room_number] || [])
            .push(Object.assign({}, p, patientRiskScore(p)));
    });

    // 환자가 없는 병실도 0점으로 남긴다 (막대가 사라지면 병실 간 비교가 깨진다)
    const rooms = ROOM_NUMBERS.map(room => {
        const members = byRoom[room] || [];
        const top = members.reduce((a, b) => (!a || b.total > a.total ? b : a), null);
        return { room, count: members.length, top, score: top ? top.total : 0 };
    }).sort((a, b) => b.score - a.score || a.room.localeCompare(b.room));

    box.textContent = "";
    rooms.forEach(r => {
        const pct = r.score ? Math.max(2, (r.score / MAX_PATIENT_SCORE) * 100) : 0;

        const row = buildRiskBarRow(r.room, r.score, pct);
        row.tabIndex = 0;                     // 마우스 없이도 상세를 볼 수 있게
        row.riskData = r;

        row.addEventListener("pointerenter", () => showRiskTip(row));
        row.addEventListener("focus", () => showRiskTip(row));
        row.addEventListener("pointerleave", hideRiskTip);
        row.addEventListener("blur", hideRiskTip);
        box.appendChild(row);
    });
}

// ===== "병실별 낙상 위험도" ↔ "오늘 순찰 횟수" 토글 =====
let monRiskMode = "risk";   // "risk" | "patrol"

function renderRoomRiskPanel() {
    if (monRiskMode === "patrol") { renderRoomPatrolToday(); }
    else { renderRoomRisk(); }
}

function setMonRiskMode(mode) {
    if (mode === monRiskMode) { return; }
    monRiskMode = mode;

    document.querySelectorAll('.mon-risk [data-risk-mode]').forEach(b => {
        b.classList.toggle("active", b.dataset.riskMode === mode);
    });

    const title = document.getElementById("mon-risk-title");
    const note = document.getElementById("mon-risk-note");
    const colLabel = document.getElementById("mon-risk-col-label");
    const foot = document.getElementById("mon-risk-foot");
    hideRiskTip();

    if (mode === "patrol") {
        if (title) title.innerText = "병실별 오늘 순찰 횟수";
        if (note) note.innerHTML = "";
        if (colLabel) colLabel.innerText = "횟수";
        if (foot) foot.style.display = "none";
    } else {
        if (title) title.innerText = "병실별 낙상 위험도";
        if (note) note.innerHTML = `순찰 우선순위 순 · 최대 <strong id="mon-risk-max">${MAX_PATIENT_SCORE}</strong>점`;
        if (colLabel) colLabel.innerText = "점수";
        if (foot) foot.style.display = "";
    }

    renderRoomRiskPanel();
}

// 병실별 "오늘" 순찰 횟수. 위험도 막대와 같은 모양(riskbar)을 그대로 재사용한다
async function renderRoomPatrolToday() {
    const box = document.getElementById("mon-risk-chart");
    if (!box) { return; }

    let rooms;
    try {
        const res = await fetch("/api/rooms/summary");
        const data = await res.json();
        if (!data.ok) { throw new Error(data.error || "실패"); }
        rooms = data.rooms;
    } catch (e) {
        box.innerHTML = '<p class="riskbars-empty">순찰 횟수를 불러오지 못했습니다.</p>';
        return;
    }

    const maxToday = Math.max(0, ...rooms.map(r => r.patrols_today || 0));
    const maxEl = document.getElementById("mon-risk-max");
    if (maxEl) { maxEl.innerText = maxToday; }

    const sorted = [...rooms].sort((a, b) =>
        (b.patrols_today || 0) - (a.patrols_today || 0) || a.room.localeCompare(b.room));

    box.textContent = "";
    sorted.forEach(r => {
        const count = r.patrols_today || 0;
        const pct = count && maxToday ? Math.max(2, (count / maxToday) * 100) : 0;
        box.appendChild(buildRiskBarRow(r.room, count, pct));
    });
}

// 막대 상세. 값이 먼저 크게, 이름은 그 다음 — 읽는 사람은 병실을 이미 알고 숫자를 원한다
function showRiskTip(row) {
    const tip = document.getElementById("mon-risk-tip");
    const r = row.riskData;
    if (!tip || !r) { return; }

    tip.textContent = "";

    const head = document.createElement("div");
    head.className = "riskbar-tip-head";
    const strong = document.createElement("strong");
    strong.textContent = r.score + "점";
    const sub = document.createElement("span");
    sub.textContent = `${r.room}호 · 재원 ${r.count}명`;
    head.append(strong, sub);
    tip.appendChild(head);

    if (r.top) {
        const who = document.createElement("p");
        who.className = "riskbar-tip-line";
        who.textContent = `${r.top.name} · ${r.top.age}세 · ${r.top.disease}`;

        const parts = document.createElement("p");
        parts.className = "riskbar-tip-parts";
        parts.textContent =
            `낙상 ${r.top.fallScore} · 연령 ${r.top.ageScore} · 질환 ${r.top.disScore}`;

        tip.append(who, parts);
    } else {
        const empty = document.createElement("p");
        empty.className = "riskbar-tip-line";
        empty.textContent = "등록된 환자가 없습니다";
        tip.appendChild(empty);
    }

    tip.hidden = false;
    // 해당 막대 바로 위에 띄우되 패널 위로 넘치지 않게 한다
    tip.style.top = Math.max(0, row.offsetTop - tip.offsetHeight - 8) + "px";
}

function hideRiskTip() {
    const tip = document.getElementById("mon-risk-tip");
    if (tip) { tip.hidden = true; }
}

// 순찰·낙상 집계는 DB 조회라 1초마다 돌릴 필요가 없다 (순찰 1회에 수십 초 단위)
// "오늘 순찰" 모드일 때만 이 주기로 같이 갱신한다 — 위험도는 아래 주석대로 폴링 대상이 아니다
setInterval(() => {
    renderRoomTally();
    if (monRiskMode === "patrol") { renderRoomPatrolToday(); }
    if (monLogTab === "fall") { loadFallLogTab(); }
}, 30000);
renderRoomTally();

// 위험도는 관리자가 환자 정보를 고칠 때만 바뀌므로 폴링하지 않는다
// (모니터링 탭을 열 때 showTab이 다시 부른다)
renderRoomRiskPanel();

// 보호자 환자 상태(확정 낙상 → 10분 후 주의)는 분 단위로 바뀌므로 20초마다 갱신
setInterval(refreshGuardianFallState, 20000);
refreshGuardianFallState();

// ===== 테마 =====
const THEMES = [
    { id: "light",    name: "라이트",     dot: "#ffffff" },
    { id: "dark",     name: "다크",       dot: "#1e293b" },
    { id: "sky",      name: "스카이",     dot: "#0ea5e9" },
    { id: "mint",     name: "민트",       dot: "#10b981" },
    { id: "lavender", name: "라벤더",     dot: "#8b5cf6" },
    { id: "peach",    name: "피치",       dot: "#f97316" },
    { id: "rose",     name: "로즈",       dot: "#e11d48" },
    { id: "sand",     name: "샌드",       dot: "#d97706" },
    { id: "graphite", name: "그래파이트", dot: "#2a2e33" },
    { id: "ocean",    name: "오션",       dot: "#123642" },
];

function applyTheme(id) {
    document.body.setAttribute("data-theme", id);
    localStorage.setItem("dashboard-theme", id);
    renderSettingsTheme();
}

// 저장된 테마 적용
document.body.setAttribute("data-theme", localStorage.getItem("dashboard-theme") || "light");

// ===== 캘린더 =====
let calYear, calMonth;   // calMonth: 0-based
let calEvents = {};
let calSelectedDate = null;

function pad2(n) { return String(n).padStart(2, "0"); }
function dateKey(y, m, d) { return `${y}-${pad2(m + 1)}-${pad2(d)}`; }

// 일정은 DB(calendar_events)에 저장되어 여러 브라우저가 공유한다.
async function fetchEvents() {
    try {
        const res = await fetch("/api/events");
        calEvents = res.ok ? await res.json() : {};
    } catch (e) {
        calEvents = {};
    }
}

// 일정 탭에 들어올 때마다 서버에서 다시 읽어 그린다
async function loadSchedule() {
    if (!document.getElementById("cal-grid")) return;
    const now = new Date();
    if (calYear === undefined) { calYear = now.getFullYear(); calMonth = now.getMonth(); }
    await fetchEvents();
    renderCalendar();
    closeDayDetail();
}

function calPrev() { calMonth--; if (calMonth < 0) { calMonth = 11; calYear--; } renderCalendar(); }
function calNext() { calMonth++; if (calMonth > 11) { calMonth = 0; calYear++; } renderCalendar(); }

function renderCalendar() {
    document.getElementById("cal-title").innerText = `${calYear}년 ${calMonth + 1}월`;
    const grid = document.getElementById("cal-grid");
    grid.innerHTML = "";

    const first = new Date(calYear, calMonth, 1).getDay(); // 0=일
    const days = new Date(calYear, calMonth + 1, 0).getDate();
    const today = new Date();
    const isThisMonth = today.getFullYear() === calYear && today.getMonth() === calMonth;

    for (let i = 0; i < first; i++) {
        const empty = document.createElement("div");
        empty.className = "cal-day empty";
        grid.appendChild(empty);
    }

    for (let d = 1; d <= days; d++) {
        const key = dateKey(calYear, calMonth, d);
        const dow = new Date(calYear, calMonth, d).getDay();
        const cell = document.createElement("div");
        cell.className = "cal-day" + (dow === 0 ? " sun" : dow === 6 ? " sat" : "") +
            (isThisMonth && today.getDate() === d ? " today" : "");
        const count = (calEvents[key] || []).length;
        cell.innerHTML = `<div class="day-num">${d}</div>` +
            (count ? `<span class="cal-badge">${count}</span>` : "");
        cell.onclick = () => openDayDetail(key);
        grid.appendChild(cell);
    }

    // 이달 등록 건수
    const badge = document.getElementById("sch-count");
    if (badge) {
        const prefix = `${calYear}-${pad2(calMonth + 1)}-`;
        badge.innerText = Object.keys(calEvents)
            .filter(k => k.startsWith(prefix))
            .reduce((sum, k) => sum + calEvents[k].length, 0);
    }
}

function openDayDetail(key) {
    calSelectedDate = key;
    document.getElementById("cal-detail-date").innerText = key;
    document.getElementById("cal-day-detail").style.display = "block";
    document.getElementById("cal-empty-hint").style.display = "none";
    document.querySelectorAll(".cal-day").forEach(c => c.classList.remove("picked"));
    renderDayEvents();
    document.getElementById("cal-event-input").focus();
}

function closeDayDetail() {
    calSelectedDate = null;
    const detail = document.getElementById("cal-day-detail");
    const hint = document.getElementById("cal-empty-hint");
    if (detail) detail.style.display = "none";
    if (hint) hint.style.display = "";
}

function renderDayEvents() {
    const list = document.getElementById("cal-event-list");
    list.innerHTML = "";
    const items = calEvents[calSelectedDate] || [];
    if (items.length === 0) {
        list.innerHTML = `<div class="cal-empty-msg">등록된 일정이 없습니다.</div>`;
        return;
    }
    items.forEach(ev => {
        const row = document.createElement("div");
        row.className = "cal-event-row";
        row.innerHTML = `<p></p><button title="삭제">&times;</button>`;
        row.querySelector("p").innerText = ev.text;
        row.querySelector("button").onclick = () => deleteEvent(ev.id);
        list.appendChild(row);
    });
}

async function addEvent() {
    const input = document.getElementById("cal-event-input");
    const text = input.value.trim();
    if (!text || !calSelectedDate) return;

    let event;
    try {
        const res = await fetch("/api/events", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ date: calSelectedDate, text })
        });
        const data = await res.json();
        if (!res.ok || !data.ok) { rcToast(data.error || "일정 추가 중 오류가 발생했습니다"); return; }
        event = data.event;
    } catch (e) {
        rcToast("서버와 통신할 수 없습니다");
        return;
    }

    (calEvents[calSelectedDate] = calEvents[calSelectedDate] || []).push(event);

    input.value = "";
    input.focus();
    renderDayEvents();
    renderCalendar();
}

async function deleteEvent(id) {
    if (!calSelectedDate) return;

    try {
        const res = await fetch("/api/events/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ date: calSelectedDate, id })
        });
        const data = await res.json();
        if (!res.ok || !data.ok) { rcToast(data.error || "일정 삭제 중 오류가 발생했습니다"); return; }
    } catch (e) {
        rcToast("서버와 통신할 수 없습니다");
        return;
    }

    calEvents[calSelectedDate] = (calEvents[calSelectedDate] || []).filter(e => e.id !== id);
    if (!calEvents[calSelectedDate].length) delete calEvents[calSelectedDate];

    renderDayEvents();
    renderCalendar();
}

// Enter 키로 일정 추가
const calInput = document.getElementById("cal-event-input");
if (calInput) calInput.addEventListener("keydown", e => { if (e.key === "Enter") addEvent(); });

// ===== 선 차트 =====
// 시간 흐름을 보여주는 차트만 선으로 그린다.
// '병실별'처럼 순서가 없는 항목은 선으로 이으면 없는 추세가 보이므로 막대를 유지한다.
const LINE_CHARTS = {
    "lc-ahome-month":  { unit: "건", data: [["4월", 3], ["5월", 5], ["6월", 4], ["7월", 7]] },
    "lc-astats-month": { unit: "건", data: [["4월", 3], ["5월", 5], ["6월", 4], ["7월", 7]] },
    "lc-astats-hour":  { unit: "건", data: [["새벽", 1], ["오전", 2], ["오후", 1], ["야간", 3]] },
    "lc-astats-day":   { unit: "회", data: [["월", 14], ["화", 16], ["수", 15], ["목", 17], ["금", 18]] },
    "lc-gstats-month": { unit: "건", data: [["4월", 0], ["5월", 0], ["6월", 1], ["7월", 2]] },
    "lc-gstats-hour":  { unit: "회", data: [["새벽", 34], ["오전", 24], ["오후", 22], ["야간", 38]] },
    "lc-gstats-week":  { unit: "회", data: [["월", 4], ["화", 5], ["수", 4], ["목", 5], ["금", 4], ["토", 4], ["일", 4]] }
};

// 병실별처럼 순서가 없는 항목은 막대로 그린다. 막대 색은 병실 위험도를 나타낸다.
const BAR_CHARTS = {
    "bc-astats-room": {
        unit: "건",
        data: [["101호", 3, "high"], ["102호", 1, "low"], ["103호", 2, "mid"], ["104호", 1, "low"]]
    }
};

const LC = { w: 340, h: 150, padL: 34, padR: 18, padTop: 16, padBottom: 30 };

// 세로축 눈금 값 (0 · 중간 · 최대)
function chartTicks(max) {
    const mid = Math.round(max / 2);
    return mid === 0 || mid === max ? [0, max] : [0, mid, max];
}

// 값이 차트 위쪽에 있으면 말풍선이 패널 제목을 가리므로 아래쪽으로 뒤집는다
function placeTip(tip, x, y) {
    tip.style.left = `${(x / LC.w) * 100}%`;
    tip.style.top = `${(y / LC.h) * 100}%`;
    tip.classList.toggle("below", y / LC.h < 0.34);
}

function yAxisHtml(max, toY) {
    return chartTicks(max)
        .map(v => `<span class="lc-y" style="top:${(toY(v) / LC.h) * 100}%">${v}</span>`)
        .join("");
}

// 선 차트와 막대 차트가 같은 그림판(LC)·같은 눈금 규칙을 쓴다.
// 값→y좌표 변환(val)과 격자선(grid)까지 여기서 한 번에 만든다.
function chartScale(rows) {
    const { w, h, padL, padR, padTop, padBottom } = LC;
    const plotW = w - padL - padR;
    const plotH = h - padTop - padBottom;
    const max = Math.max(...rows.map(r => r[1]), 1);
    const val = v => padTop + plotH * (1 - v / max);
    const grid = chartTicks(max)
        .map(v => `<line x1="${padL}" y1="${val(v)}" x2="${w - padR}" y2="${val(v)}"/>`)
        .join("");
    return { w, h, padL, padTop, plotW, plotH, baseY: padTop + plotH, max, val, grid };
}

// x축 라벨. 막대는 칸 가운데, 선은 점 위치라 자리 계산(at)만 각자 넘긴다
function chartXLabels(rows, at) {
    const { w } = LC;
    return rows.map((r, i) =>
        `<span class="lc-x" style="left:${(at(i) / w) * 100}%">${r[0]}</span>`).join("");
}

function renderLineChart(el) {
    const chart = LINE_CHARTS[el.id];
    if (!chart) return;

    const rows = chart.data;
    // 데이터가 0개면 pts[0]이 undefined라 밑에서 죽는다 — 여기서 막아야 이 뒤에 그릴
    // 다른 차트들까지(같은 forEach 반복문 안이라) 같이 멈추는 걸 막을 수 있다.
    if (!rows.length) {
        el.innerHTML = "";
        return;
    }

    const { w, h, padL, padTop, plotW, baseY, max, val, grid } = chartScale(rows);
    const n = rows.length;
    const stepX = n > 1 ? plotW / (n - 1) : 0;
    const at = i => padL + i * stepX;

    const pts = rows.map((r, i) => [at(i), val(r[1])]);
    const line = pts.map(p => `${p[0]} ${p[1]}`).join(" L ");
    const area = `M ${pts[0][0]} ${baseY} L ${line} L ${pts[n - 1][0]} ${baseY} Z`;

    const dots = pts.map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="4"/>`).join("");
    const labels = chartXLabels(rows, at);

    el.innerHTML =
        `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
             <g class="lc-grid">${grid}</g>
             <path class="lc-area" d="${area}"/>
             <path class="lc-line" d="M ${line}"/>
             <g class="lc-dots">${dots}</g>
             <line class="lc-cursor" y1="${padTop}" y2="${baseY}" style="display:none"/>
         </svg>
         <div class="lc-yaxis">${yAxisHtml(max, val)}</div>
         <div class="lc-labels">${labels}</div>
         <div class="lc-tip"></div>`;

    const tip = el.querySelector(".lc-tip");
    const cursor = el.querySelector(".lc-cursor");

    function show(i) {
        const [x, y] = pts[i];
        tip.innerHTML = `<span>${rows[i][0]}</span><strong>${rows[i][1]}${chart.unit}</strong>`;
        placeTip(tip, x, y);
        tip.classList.add("on");
        cursor.setAttribute("x1", x);
        cursor.setAttribute("x2", x);
        cursor.style.display = "";
        el.querySelectorAll(".lc-dots circle")
          .forEach((c, k) => c.classList.toggle("on", k === i));
    }

    function hide() {
        tip.classList.remove("on");
        cursor.style.display = "none";
        el.querySelectorAll(".lc-dots circle").forEach(c => c.classList.remove("on"));
    }

    el.onmousemove = e => {
        const box = el.getBoundingClientRect();
        if (!box.width) return;                                   // 숨겨진 탭이면 계산이 NaN이 된다
        const vx = ((e.clientX - box.left) / box.width) * w;      // viewBox 좌표로 환산
        let i = Math.round((vx - padL) / (stepX || 1));
        if (!Number.isFinite(i)) return;
        i = Math.max(0, Math.min(n - 1, i));
        show(i);
    };
    el.onmouseleave = hide;
}

function renderBarChart(el) {
    const chart = BAR_CHARTS[el.id];
    if (!chart) return;

    const rows = chart.data;
    const { w, h, padL, padTop, plotW, baseY, max, val, grid } = chartScale(rows);
    const n = rows.length;
    const slot = plotW / n;
    const barW = Math.min(slot * 0.5, 26);
    const at = i => padL + slot * (i + 0.5);

    const bars = rows.map((r, i) =>
        `<rect class="bc-bar bc-${r[2]}" x="${at(i) - barW / 2}" y="${val(r[1])}" ` +
        `width="${barW}" height="${Math.max(baseY - val(r[1]), 1)}"/>`).join("");
    const labels = chartXLabels(rows, at);

    el.innerHTML =
        `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
             <g class="lc-grid">${grid}</g>
             <g class="bc-bars">${bars}</g>
         </svg>
         <div class="lc-yaxis">${yAxisHtml(max, val)}</div>
         <div class="lc-labels">${labels}</div>
         <div class="lc-tip"></div>`;

    const tip = el.querySelector(".lc-tip");

    el.onmousemove = e => {
        const box = el.getBoundingClientRect();
        if (!box.width) return;
        const vx = ((e.clientX - box.left) / box.width) * w;
        let i = Math.floor((vx - padL) / (slot || 1));
        if (!Number.isFinite(i)) return;
        i = Math.max(0, Math.min(n - 1, i));
        const [label, value, risk] = rows[i];
        tip.innerHTML = `<span>${label} · ${RISK_TEXT[risk]}</span><strong>${value}${chart.unit}</strong>`;
        placeTip(tip, at(i), val(value));
        tip.classList.add("on");
        el.querySelectorAll(".bc-bar").forEach((b, k) => b.classList.toggle("on", k === i));
    };

    el.onmouseleave = () => {
        tip.classList.remove("on");
        el.querySelectorAll(".bc-bar").forEach(b => b.classList.remove("on"));
    };
}

// 관리자 통계 차트 id 목록. 실패 처리에서도 같은 목록을 쓴다
const ADMIN_STAT_CHARTS = [
    "lc-ahome-month", "lc-astats-month", "lc-astats-hour", "lc-astats-day", "bc-astats-room"
];
const ADMIN_STAT_TILES = [
    "a-stat-rooms", "a-stat-risk-rooms", "a-stat-today-falls", "a-stat-month-falls"
];

// 통계를 못 받았을 때 자리표시용 숫자를 그대로 두면 가짜 값이 진짜처럼 보인다.
// 자리표시 데이터를 아예 지우고 화면에 실패를 드러낸다.
// (LINE_CHARTS/BAR_CHARTS에서 지우면 renderCharts가 그 element를 건드리지 않으므로
//  여기서 넣은 안내 문구가 그대로 남는다)
function markAdminStatsFailed(reason) {
    console.error("[stats] 관리자 통계를 불러오지 못했습니다:", reason);

    ADMIN_STAT_CHARTS.forEach(id => {
        delete LINE_CHARTS[id];
        delete BAR_CHARTS[id];
        const el = document.getElementById(id);
        if (el) {
            el.textContent = "";
            const p = document.createElement("p");
            p.className = "lc-empty";
            p.textContent = "통계를 불러오지 못했습니다.";
            el.appendChild(p);
        }
    });

    ADMIN_STAT_TILES.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = "—";   // 단위 span까지 같이 지운다
    });
}


async function loadAdminStats() {
    try {
        const res = await fetch("/api/stats/admin");
        // 보호자 계정이면 이 API를 쓸 일이 없다. 401/403은 실패 표시 없이 조용히 넘긴다
        if (res.status === 401 || res.status === 403) { return; }
        const data = await res.json();
        if (!data.ok) { markAdminStatsFailed(data.error || `서버 응답 ${res.status}`); return; }
        LINE_CHARTS["lc-ahome-month"] = { unit: "건", data: data.monthly_falls };
        LINE_CHARTS["lc-astats-month"] = { unit: "건", data: data.monthly_falls };
        LINE_CHARTS["lc-astats-hour"] = { unit: "건", data: data.hourly_falls };
        LINE_CHARTS["lc-astats-day"] = { unit: "회", data: data.daily_patrols };
        BAR_CHARTS["bc-astats-room"] = { unit: "건", data: data.room_falls };

        const setNum = (id, v, unit) => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = v + `<span class="stat-tile-unit">${unit}</span>`;
        };
        setNum("a-stat-rooms", data.total_rooms, "실");
        setNum("a-stat-risk-rooms", data.risk_rooms, "실");
        setNum("a-stat-today-falls", data.today_falls, "건");
        setNum("a-stat-month-falls", data.month_falls, "건");
    } catch (e) {
        markAdminStatsFailed(e);
    }
}

async function loadGuardianStats() {
    try {
        const res = await fetch("/api/stats/guardian");
        const data = await res.json();
        // 연동된 환자가 없으면(관리자 계정 등) 서버가 빈 배열을 주므로 여기서 멈춘다.
        // 그냥 진행하면 renderLineChart가 빈 배열에서 죽어서 뒤에 그릴 다른 차트까지 다 같이 멈춘다.
        if (!data.ok || !data.daily_patrols.length) return;
        LINE_CHARTS["lc-gstats-month"] = { unit: "건", data: data.monthly_falls };
        LINE_CHARTS["lc-gstats-hour"] = { unit: "회", data: data.hourly_patrols };
        LINE_CHARTS["lc-gstats-week"] = { unit: "회", data: data.daily_patrols };

        const set = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v; };
        // stat-tile-num 안에는 단위 span이 중첩돼 있어서 innerText로 덮으면 단위가 같이 지워진다
        const setNum = (id, v, unit) => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = v + `<span class="stat-tile-unit">${unit}</span>`;
        };
        set("g-live-today", data.patrol_today + "회");
        setNum("g-stat-today", data.patrol_today, "회");
        setNum("g-stat-patrol30", data.patrol_30d, "회");
        set("g-stat-patrol30-sub", `하루 평균 ${Math.round(data.patrol_30d / 30 * 10) / 10}회`);
    } catch (e) { /* 실패하면 기존 값 유지 */ }
}

async function renderCharts() {
    await Promise.all([loadAdminStats(), loadGuardianStats()]);
    document.querySelectorAll(".lc").forEach(el => {
        if (BAR_CHARTS[el.id]) renderBarChart(el);
        else renderLineChart(el);
    });
}

// ===== 탭 콘텐츠 (각 API 연동) =====

// --- 환자 관리 (관리자) --- 
async function renderPatients() {
    const tb = document.getElementById("pt-tbody");
    if (!tb) return;
    WARDS = await fetchWards();
    tb.innerHTML = "";
    WARDS.forEach((w, i) => {
        const tr = document.createElement("tr");
        tr.innerHTML =
            h`<td>${w.room}호</td><td>${w.name}</td><td>${w.age}세 · ${w.sex}</td><td>${w.diagnosis}</td>` +
            h`<td><span class="risk-chip risk-${RISK_CLASS[w.risk] || "idle"}">${w.risk}</span></td>` +
            h`<td>${w.lastFall}</td>` +
            `<td class="row-actions"><button class="mini-btn mini-danger" onclick="dischargePatient(${i})">퇴원 처리</button></td>`;
        tb.appendChild(tr);
    });
    const count = document.getElementById("pt-count");
    if (count) count.innerText = WARDS.length;
}

async function addPatient() {
    const val = id => document.getElementById(id).value.trim();
    const room = val("pt-room"), name = val("pt-name");
    const age = val("pt-age"), diagnosis = val("pt-diag");
    const err = document.getElementById("pt-error");

    if (!ROOM_INFO[room]) { err.innerText = "병실은 101~104호만 등록할 수 있습니다."; return; }

    if (name.length < 2) { err.innerText = "환자 이름을 2자 이상 입력해 주세요."; return; }

    // 같은 병실에 이름이 이미 있으면 새 입원이 아니라 정보 수정이므로 정원 체크는 건너뛴다
    const isEdit = WARDS.some(w => w.room === room && w.name.replace(/\s/g, "") === name.replace(/\s/g, ""));
    if (!isEdit) {
        // 병실 정원까지만 새로 등록할 수 있다 (ROOM_INFO의 capacity)
        const cap = roomCapacityOf(room);
        const inRoom = WARDS.filter(w => w.room === room).length;
        if (cap && inRoom >= cap) {
            err.innerText = `${room}호는 ${ROOM_INFO[room].type} 정원 ${cap}명이 모두 찼습니다.`;
            return;
        }
    }
    if (!age || +age < 1 || +age > 120) { err.innerText = "나이를 1~120 사이로 입력해 주세요."; return; }
    if (diagnosis.length < 2) { err.innerText = "병명을 2자 이상 입력해 주세요."; return; }

    const sex = document.getElementById("pt-sex").value;
    const risk = document.getElementById("pt-risk").value;

    try {
        const res = await fetch("/api/patients", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ room, name, age: +age, sex, disease: diagnosis, risk_level: risk })
        });
        const data = await res.json();
        if (!res.ok || !data.ok) { err.innerText = data.error || "등록 중 오류가 발생했습니다."; return; }
    } catch (e) {
        err.innerText = "서버와 통신할 수 없습니다.";
        return;
    }

    ["pt-room", "pt-name", "pt-age", "pt-diag"].forEach(id => { document.getElementById(id).value = ""; });
    err.innerText = "";
    renderPatients();
    renderCharts();   // Home 요약 카드(전체/위험 병실)·통계 차트도 위험도 변경을 바로 반영
    rcToast(name + " 님을 " + room + "호에 등록했습니다");
}

async function dischargePatient(i) {
    const w = WARDS[i];
    // 연동된 보호자가 있으면 계정이 붕 뜨므로 '계정 관리 → 보호자 계정'에서 처리하게 한다
    // 캐시된 GUARDIANS는 최신이 아닐 수 있어(로그인 이후 새로 연동됐을 수 있음) 여기서 다시 받아온다
    GUARDIANS = await fetchGuardians();
    const linked = GUARDIANS.filter(g => {
        const ref = parsePatientRef(g.patient);
        return !g.discharged && ref.room === w.room && ref.name === w.name;
    });
    if (linked.length) {
        rcToast(w.name + " 님과 연동된 보호자 계정(" + linked.map(g => g.username).join(", ") + ")이 있어 여기서는 퇴원 처리할 수 없습니다");
        return;
    }
    askConfirm({
        title: "환자 퇴원 처리",
        body: h`<b>${w.room}호 ${w.name}</b> 님을 퇴원 처리합니다.<br>재원 목록과 병실 모니터링에서 함께 사라집니다.`,
        okText: "퇴원 처리",
        danger: true,
        onOk: async () => {
            try {
                const res = await fetch(`/api/patients/${w.id}/delete`, { method: "POST" });
                const data = await res.json();
                if (!res.ok || !data.ok) { rcToast(data.error || "퇴원 처리 중 오류가 발생했습니다"); return; }
            } catch (e) {
                rcToast("서버와 통신할 수 없습니다");
                return;
            }
            renderPatients();
            renderCharts();   // 전체/위험 병실 수 변동 반영
            rcToast(w.name + " 님을 퇴원 처리했습니다");
        }
    });
}

// --- 낙상 이력 / 통계 --- 
async function renderFalls() {
    let rows = [];
    try {
        const res = await fetch("/api/fall-log");
        const data = await res.json();
        rows = data.ok ? data.logs : [];
    } catch (e) { /* 네트워크 오류 시 빈 목록으로 처리 */ }

    const tb = document.getElementById("falls-tbody");
    if (tb) {
        tb.innerHTML = "";
        rows.forEach(f => {
            const tr = document.createElement("tr");
            const who = f.patient_name ? esc(f.patient_name) : '<span class="ev-unknown">-</span>';
            tr.innerHTML = h`<td>${f.detected_at}</td><td>병실 ${f.room_number}</td><td>${raw(who)}</td>`;
            tb.appendChild(tr);
        });
    }

    // Home 탭 "최근 낙상" (최근 20건, 날짜만 짧게, 스크롤)
    const ahomeFalls = document.getElementById("ahome-falls");
    if (ahomeFalls) {
        ahomeFalls.innerHTML = rows.slice(0, 20).map(f =>
            h`<li><span class="tl-time">${f.detected_at.slice(5, 10)}</span><span>병실 ${f.room_number}</span></li>`
        ).join("") || '<li class="g-log-empty">낙상 기록이 없습니다.</li>';
    }
}

// --- 설정: 테마 ---
function renderSettingsTheme() {
    const wrap = document.getElementById("settings-theme");
    if (!wrap) return;
    const cur = document.body.getAttribute("data-theme") || "light";
    wrap.innerHTML = "";
    THEMES.forEach(t => {
        const el = document.createElement("button");
        el.className = "theme-swatch" + (t.id === cur ? " selected" : "");
        el.onclick = () => { applyTheme(t.id); renderSettingsTheme(); };
        el.innerHTML = `<span class="theme-dot" style="background:${t.dot}"></span>${t.name}`;
        wrap.appendChild(el);
    });
}

// ===== Robot Control 토스트 =====
/* ----- Robot Control -----
   지도에서 목적지를 누르면 POST /api/robot/goto/<place> 로 실제 이동 명령이 나간다.
   place 는 rooms.yaml 의 키(room1 / dock / standby ...) */
function rcSetState(state) {
    ["rc-state", "rc-route-state"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerText = state;
    });
}

// 목적지 표시. 현재 위치는 rcSetCurrent()가 실제 좌표로 따로 채운다.
// 예전에는 목적지를 누르면 현재 위치까지 같이 바뀌었는데, 그건 아직 출발도
// 안 한 시점이라 거짓 표시였다
function rcSetPlace(dest) {
    const label = document.getElementById("rc-dest-label");
    if (label) label.innerText = dest;
}

// 현재 위치 표시 (경로 패널 · 로봇 상태). AMCL 좌표가 든 구역 이름
function rcSetCurrent(place) {
    ["rc-cur", "rc-cur2"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerText = place;
    });
}

// 이동 요청이 날아가 있는 동안 중복 클릭을 막는다 (rcGoTo에서 사용)
let rcGoBusy = false;

// ===== 이동 목적지 — rooms.yaml이 원본 =====
// 좌표와 이름을 화면에 또 적어두면 waypoint_saver로 좌표를 다시 찍었을 때
// 화면과 로봇이 어긋난다. 그래서 서버(/api/robot/places)를 통해 받아온다.
// { room1: {label, room_number, x, y}, ... }
let RC_PLACES = {};

async function rcLoadPlaces() {
    try {
        const res  = await fetch("/api/robot/places");
        const data = await res.json();

        // 실패를 조용히 넘기면 Waypoint 표가 "불러오는 중"에 멈춘 채로 남아서
        // 원인을 알 수 없다. 화면에 이유를 띄운다
        if (!data.ok) {
            rcRenderWaypointsError(data.error || "좌표를 불러오지 못했습니다.");
            return;
        }

        RC_PLACES = {};
        data.places.forEach(p => { RC_PLACES[p.key] = p; });

        // 도면의 지점 이름도 yaml 값으로 맞춘다
        document.querySelectorAll(".rc-spot").forEach(spot => {
            const p = RC_PLACES[spot.dataset.place];
            if (p) { spot.dataset.dest = p.label; }
        });

        rcRenderWaypoints(data.places);
        syncRoomLabels();   // 도면의 "n인실" 라벨은 병실 번호를 알아야 채울 수 있다
    } catch (e) {
        console.error(e);
        rcRenderWaypointsError("서버에 연결하지 못했습니다. (F12 콘솔 확인)");
    }
}

// Waypoint 표에 실패 사유를 남긴다
function rcRenderWaypointsError(message) {
    const tbody = document.getElementById("rc-wp-tbody");
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="5">${message}</td></tr>`;
    }
}

// SLAM · Waypoint 표. 등록된 좌표를 그대로 보여준다
function rcRenderWaypoints(places) {
    const tbody = document.getElementById("rc-wp-tbody");
    if (!tbody) { return; }

    if (!places.length) {
        tbody.innerHTML = '<tr><td colspan="5">등록된 좌표가 없습니다. ' +
                          'waypoint_saver로 먼저 저장해 주세요.</td></tr>';
        return;
    }

    tbody.innerHTML = places.map((p, i) =>
        h`<tr><td>${i + 1}</td><td>wp_${p.key}</td><td>${p.label}</td>` +
        h`<td>${p.x.toFixed(2)}, ${p.y.toFixed(2)}</td>` +
        `<td><span class="risk-chip risk-low">등록됨</span></td></tr>`
    ).join("");
}

// 목적지 목록은 좌표를 다시 찍었을 때만 바뀌므로 화면을 열 때 한 번만 받아온다
rcLoadPlaces();

// 로봇 아이콘을 도면 위 한 지점으로 옮긴다. 단위는 건물 좌표계(SVG user unit).
// 관리자 지도(rm-robot)와 보호자 지도(g-live-robot)가 같이 쓴다.
function placeRobotIcon(elId, x, y) {
    const robot = document.getElementById(elId);
    if (robot) {
        robot.style.transform = `translate(${x}px, ${y}px)`;
    }
}

// AMCL 위치가 들어오는 동안은 실제 위치가 우선이므로, 클릭으로 아이콘을 옮기지 않는다.
// (안 그러면 1초 뒤 폴링이 제자리로 되돌려서 깜빡인다)
let rcMapLive = false;

function rcSetMapLive(on) {
    if (on === rcMapLive) { return; }
    rcMapLive = on;
    const map = document.getElementById("rc-map");
    if (map) { map.classList.toggle("rc-map-stale", !on); }
}

// ===== ROS map 좌표(m) → 도면 좌표(SVG unit) 변환 =====
//
// 회전 + 확대 + 이동 + 상하반전을 한꺼번에 처리한다.
//   반전이 필요한 이유: ROS는 y축이 위로, 화면은 y축이 아래로 증가한다.
//   회전이 필요한 이유: 건물이 ROS 맵 축에 대해 비스듬히 놓여 있다(SLAM 결과).
//
//   plan_x =  A*map_x + B*map_y + TX
//   plan_y =  B*map_x - A*map_y + TY
//
// 미지수가 4개(A, B, TX, TY)라 기준점 2개면 정확히 풀린다.
// waypoint_saver로 좌표를 찍은 뒤 아래 map: [x, y] 두 줄만 채우면 되고,
// 각도나 배율을 직접 잴 필요는 없다. 오차를 줄이려면 서로 가장 먼 두 점을 쓴다.
// 2026-08-10 실측. rooms.yaml의 값을 그대로 옮긴 것이라,
// 좌표를 다시 찍으면 이 두 줄도 같이 갱신해야 한다.
const MAP_ANCHORS = [
    { map: [-2.132, 3.181], plan: [ 80.4, 416.1] },   // dock
    { map: [ 0.882, 0.315], plan: [630.5, 125.8] },   // room4.inside
];

const MAP_TF = (function () {
    const [p, q] = MAP_ANCHORS;
    if (!p.map || !q.map) { return null; }   // 아직 좌표를 안 찍음

    const dmx = q.map[0]  - p.map[0],  dmy = q.map[1]  - p.map[1];
    const dpx = q.plan[0] - p.plan[0], dpy = q.plan[1] - p.plan[1];

    const det = dmx * dmx + dmy * dmy;
    if (det < 1e-9) { return null; }         // 두 기준점이 같은 자리

    const A = (dmx * dpx - dmy * dpy) / det;
    const B = (dmy * dpx + dmx * dpy) / det;

    return {
        A, B,
        TX: p.plan[0] - (A * p.map[0] + B * p.map[1]),
        TY: p.plan[1] - (B * p.map[0] - A * p.map[1]),
    };
})();

// ROS map 좌표 한 점 → 도면 좌표 [x, y]. 보정 전이면 null
function rcMapToPlan(mx, my) {
    if (!MAP_TF) { return null; }
    return [MAP_TF.A * mx + MAP_TF.B * my + MAP_TF.TX,
            MAP_TF.B * mx - MAP_TF.A * my + MAP_TF.TY];
}

// 실시간 위치 표시. map 좌표(m)를 받아 도면 좌표로 바꿔서 옮긴다
function placeRobotIconByMap(elId, mx, my) {
    const p = rcMapToPlan(mx, my);
    if (p) { placeRobotIcon(elId, p[0], p[1]); }
}

// ===== 로봇이 지금 어느 구역에 있나 =====
// 도면 좌표가 어느 구역 사각형 안에 들어가는지로 판정한다. 구역 크기는 SVG의
// <rect>에서 직접 읽으므로, 도면을 넓히거나 늘려도 여기를 같이 고칠 필요가 없다.
// 반환값은 rooms.yaml의 키(room1 / dock / ...), 어느 구역에도 안 들면 null(=복도).
function rcZoneKeyAt(px, py) {
    for (const spot of document.querySelectorAll(".rc-spot")) {
        const rect = spot.querySelector("rect");
        if (!rect) { continue; }

        const x = +rect.getAttribute("x"), y = +rect.getAttribute("y");
        const w = +rect.getAttribute("width"), h = +rect.getAttribute("height");

        if (px >= x && px <= x + w && py >= y && py <= y + h) {
            return spot.dataset.place;
        }
    }
    return null;
}

// ROS map 좌표 -> 구역 키. 아직 도면 보정(MAP_ANCHORS) 전이면 undefined.
// null(복도)과 구분해야 해서 반환값이 셋이다
function rcZoneKeyByMap(mx, my) {
    const p = rcMapToPlan(mx, my);
    return p ? rcZoneKeyAt(p[0], p[1]) : undefined;
}

// 구역 키 -> 화면에 쓸 이름
function rcZoneLabel(key) {
    if (!key) { return "복도"; }
    return (RC_PLACES[key] || {}).label || key;
}

// 로봇이 있는 병실 번호("101" 등). 병실 밖이면 null.
// 좌표 판정을 우선하고, 좌표가 없거나 보정 전이면 ArUco 마커 값으로 대체한다
function robotRoomNumber(data) {
    if (typeof data.robot_x === "number" && typeof data.robot_y === "number") {
        const key = rcZoneKeyByMap(data.robot_x, data.robot_y);
        if (key !== undefined) {
            return (RC_PLACES[key] || {}).room_number || null;
        }
    }
    return data.current_room || null;
}

// Nav2가 계획한 경로 그리기. pts 는 [[x, y], ...] (map 좌표계)
// 관리자 지도(rc-path)와 보호자 지도(g-live-path)가 같이 쓴다.
function drawMapPath(elId, pts) {
    const el = document.getElementById(elId);
    if (!el) { return; }

    if (!MAP_TF || !Array.isArray(pts) || pts.length < 2) {
        el.setAttribute("points", "");
        return;
    }

    const plan = [];
    for (const [mx, my] of pts) {
        const p = rcMapToPlan(mx, my);
        if (p) { plan.push(p[0].toFixed(1) + "," + p[1].toFixed(1)); }
    }
    el.setAttribute("points", plan.join(" "));
}

// ===== 보호자용 지도(g-live-map) — 열람 전용, 로봇 위치만 실시간으로 따라 그린다 =====
// 아이콘·경로는 위 공용 함수를 쓰고, 여기서는 '실시간 여부' 표시만 따로 둔다.
// rcSetMapLive 와 합치지 않는 이유: 그쪽은 rcMapLive 플래그로 클릭 이동을 억제하는데,
// 보호자 폴링이 그 플래그를 건드리면 관리자 지도의 클릭 동작이 바뀐다.
function gLiveSetMapLive(on) {
    const map = document.getElementById("g-live-map");
    if (map) { map.classList.toggle("rc-map-stale", !on); }
}

// 보정 검산용. 기준점으로 안 쓴 제3의 지점(예: 대기 데스크)의 ROS 좌표를 넣어
// 콘솔에서 rcCheckMap(x, y) 를 부르면 도면 좌표가 찍힌다. (360, 290) 근처면 성공
function rcCheckMap(mx, my) {
    const p = rcMapToPlan(mx, my);
    console.log(p ? ["plan =", p[0], p[1]] : "MAP_ANCHORS가 아직 비어 있습니다");
}

async function rcGoTo(btn) {
    const place = btn.dataset.place;
    // 이름은 rooms.yaml에서 온 값을 우선 쓴다. 아직 목록을 못 받았으면 도면에 적힌 이름으로
    const dest = (RC_PLACES[place] || {}).label || btn.dataset.dest;

    document.querySelectorAll(".rc-spot").forEach(s => s.classList.remove("target"));
    btn.classList.add("target");

    if (!place) {                       // 도면에만 있고 좌표가 없는 지점
        rcToast("등록되지 않은 목적지입니다");
        btn.classList.remove("target");
        return;
    }

    // 로봇은 한 번에 한 곳만 간다. 맵의 지점과 옆 패널 버튼이 서로 다른 element라
    // btn.disabled로는 중복 클릭을 못 막아서 플래그로 잠근다
    if (rcGoBusy) { return; }
    rcGoBusy = true;

    try {
        const res  = await fetch("/api/robot/goto/" + place, { method: "POST" });
        const data = await res.json();

        if (data.ok) {
            // 로봇 아이콘은 건드리지 않는다. 1초 폴링이 TF 실제 위치로 그린다
            rcSetState(place === "dock" ? "복귀 중" : "이동 중");
            rcSetPlace(dest);
            rcToast(dest + "(으)로 이동합니다");
        } else {
            btn.classList.remove("target");
            rcToast(data.error || data.message || "이동 명령에 실패했습니다");
        }
    } catch (e) {
        btn.classList.remove("target");
        rcToast("요청을 보내지 못했습니다");
    } finally {
        rcGoBusy = false;
    }
}

// ===== 일시정지 · 이어서 이동 =====
// 로봇은 한 번에 한 곳만 가므로, 이동 중에 다른 목적지를 찍으려면 먼저 멈춰야 한다.
// 일시정지는 Nav2 goal을 취소하는 것이고, 남은 경유점은 로봇이 들고 있다가
// '이어서 이동'을 누르면 취소된 지점부터 다시 간다.
let rcPaused = false;
let rcPauseBusy = false;

function rcSyncPauseButton(paused) {
    if (paused === rcPaused) { return; }
    rcPaused = paused;

    const btn = document.getElementById("rc-pause-btn");
    if (btn) { btn.innerText = paused ? "이어서 이동" : "일시정지"; }
}

async function rcTogglePause() {
    if (rcPauseBusy) { return; }
    rcPauseBusy = true;

    const resume = rcPaused;

    try {
        const res  = await fetch(resume ? "/api/robot/resume" : "/api/robot/pause",
                                 { method: "POST" });
        const data = await res.json();

        if (data.ok) {
            rcSyncPauseButton(!resume);
            rcSetState(resume ? "이동 중" : "일시정지");
            rcToast(data.message || (resume ? "이동을 다시 시작합니다" : "이동을 일시정지했습니다"));
        } else {
            rcToast(data.error || data.message || "명령에 실패했습니다");
        }
    } catch (e) {
        rcToast("요청을 보내지 못했습니다");
    } finally {
        rcPauseBusy = false;
    }
}

// 버튼으로 이동 (대기 장소 · 충전 스테이션). place = rooms.yaml의 키
function rcGoDest(place) {
    const spot = [...document.querySelectorAll(".rc-spot")].find(s => s.dataset.place === place);
    if (spot) {
        rcGoTo(spot);   // 명령 전송·상태 표시·토스트는 rcGoTo가 처리한다
        return;
    }
    rcToast("도면에 없는 목적지입니다");
}


// 순찰 시작 · 일시정지 · 비상 정지
function rcCommand(label, state) {
    rcSetState(state);
    rcToast(label === "비상 정지" ? "비상 정지되었습니다" : label + " 명령을 보냈습니다");
}

// ===== 확인 모달 =====
// 되돌리기 어려운 동작(삭제·퇴원·승인) 앞에 한 번 더 물어본다
let confirmAction = null;

function askConfirm(opts) {
    document.getElementById("cf-title").innerText = opts.title;
    document.getElementById("cf-body").innerHTML = opts.body;
    const ok = document.getElementById("cf-ok");
    ok.innerText = opts.okText || "확인";
    ok.classList.toggle("danger", !!opts.danger);
    confirmAction = opts.onOk;
    document.getElementById("cf-overlay").classList.add("open");
    ok.focus();
}

function closeConfirm() {
    document.getElementById("cf-overlay").classList.remove("open");
    confirmAction = null;
}

function confirmOk() {
    const run = confirmAction;
    closeConfirm();
    if (run) run();
}

function rcToast(msg) {
    // Robot Control 패널이 안 보이는 화면(보호자 등)에서는 화면 하단 공용 토스트를 쓴다
    const rc = document.getElementById("rc-toast");
    const el = (rc && rc.offsetParent !== null) ? rc : document.getElementById("app-toast");
    if (!el) { return; }
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(rcToast._t);
    rcToast._t = setTimeout(() => el.classList.remove("show"), 1800);
}

// ===== 이벤트 로그 ===== 
// 순찰·호출 로그는 '병실 모니터링'의 실시간 이벤트 로그에서 보므로,
// 이 탭은 간호사가 확인·처리해야 하는 낙상(fall_log)만 다룬다
let FALL_LOGS = [];

async function renderEvents() {
    const tb = document.getElementById("events-tbody");
    if (!tb) return;
    try {
        const res = await fetch("/api/fall-log");
        const data = await res.json();
        FALL_LOGS = data.ok ? data.logs : [];
    } catch (e) {
        FALL_LOGS = [];
    }
    renderEventRows(FALL_LOGS);
}

function renderEventRows(rows) {
    const tb = document.getElementById("events-tbody");
    if (!tb) return;
    tb.innerHTML = "";
    if (!rows.length) {
        tb.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted);">검색 결과가 없습니다.</td></tr>';
    } else {
        rows.forEach(e => {
            const tr = document.createElement("tr");
            // 간호사가 확인해 지정한 환자를 함께 보여준다
            // 퇴원한 환자도 확정 당시 이름이 남아 있다. 지금 재원 중이 아님을 함께 표시한다
            const who = e.patient_name
                ? h` · <b class="ev-who">${e.patient_name} 님</b>` +
                  (e.discharged ? ' <span class="ev-unknown">(퇴원)</span>' : "")
                : ' · <span class="ev-unknown">-</span>';
            tr.innerHTML =
                h`<td>${e.detected_at}</td><td>병실 ${e.room_number}</td><td>낙상 감지${raw(who)}</td>` +
                `<td>${e.done
                    ? '<span class="risk-chip risk-low">대응 완료</span>'
                    : '<span class="risk-chip risk-mid">미처리</span>'}</td>` +
                `<td class="row-actions">` +
                    `<button class="mini-btn" onclick="openEvCapture(${e.id})">${e.done ? "📷 보기" : "처리"}</button>` +
                    `${e.done && e.memo ? h`<span class="a-memo" title="${e.memo}">📝 메모</span>` : ""}` +
                `</td>`;
            tb.appendChild(tr);
        });
    }
    const pending = FALL_LOGS.filter(e => !e.done).length;
    const badge = document.getElementById("ev-pending-count");
    if (badge) badge.innerText = pending;
}
function filterEvents() {
    const q = (document.getElementById("ev-search").value || "").trim();
    const d = document.getElementById("ev-date").value;
    renderEventRows(FALL_LOGS.filter(e => {
        const matchQ = !q || (e.room_number + "낙상감지").includes(q);
        const matchD = !d || e.detected_at.startsWith(d);
        return matchQ && matchD;
    }));
}
function clearEventFilter() {
    document.getElementById("ev-search").value = "";
    document.getElementById("ev-date").value = "";
    renderEventRows(FALL_LOGS);
}


renderPatients();
renderCharts();
renderFalls();
renderEvents();
renderSettingsTheme();
renderRoomOptions();   // 환자 등록 병실 선택지 (101~104호)
syncRoomLabels();      // 화면의 "n인실" 라벨을 ROOM_INFO에 맞춤
renderPending();
renderGuardians();
renderNurses();
renderNav();
renderGuardian();
goHome();
showRoom("r4");     // 병실 소개 기본값 = 4인실
initReveal();
initHeroScroll();
initFeatures();
