// ---------------------------------------------------------------------------
// Toast notifications
// ---------------------------------------------------------------------------
function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const colors = {
        info: "bg-blue-600",
        success: "bg-green-600",
        error: "bg-red-600",
        warning: "bg-yellow-600",
    };
    const toast = document.createElement("div");
    toast.className = `${colors[type] || colors.info} text-white px-4 py-2 rounded-lg text-sm shadow-lg transition-opacity duration-300`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------
async function api(url, opts = {}) {
    const defaults = { headers: { "Content-Type": "application/json" } };
    const res = await fetch(url, { ...defaults, ...opts });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Request failed");
    return data;
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
async function loadDashboardStats() {
    try {
        const s = await api("/api/dashboard/stats");
        setText("stat-accounts", s.accounts);
        setText("stat-today-followed", s.today_followed);
        setText("stat-today-unfollowed", s.today_unfollowed);
        setText("stat-followback-ratio", s.followback_ratio + "%");
    } catch (_) {}
}

let dashboardChart = null;
async function loadDashboardChart(accountId) {
    try {
        const data = await api(`/api/accounts/${accountId}/daily-stats?days=14`);
        const labels = data.map((d) => d.day.slice(5));
        const follows = data.map((d) => d.follow || 0);
        const unfollows = data.map((d) => d.unfollow || 0);
        const likes = data.map((d) => d.like || 0);

        if (dashboardChart) dashboardChart.destroy();
        const ctx = document.getElementById("activityChart");
        if (!ctx) return;
        dashboardChart = new Chart(ctx, {
            type: "bar",
            data: {
                labels,
                datasets: [
                    { label: "Follows", data: follows, backgroundColor: "#22c55e" },
                    { label: "Unfollows", data: unfollows, backgroundColor: "#ef4444" },
                    { label: "Likes", data: likes, backgroundColor: "#eab308" },
                ],
            },
            options: chartOptions(),
        });
    } catch (_) {}
}

async function loadGlobalActivity() {
    try {
        const data = await api("/api/activity?limit=20");
        const el = document.getElementById("activity-feed");
        if (!el) return;
        if (!data.length) {
            el.innerHTML = '<p class="text-gray-600 text-sm">No activity yet</p>';
            return;
        }
        el.innerHTML = data.map((a) => activityItem(a, true)).join("");
    } catch (_) {}
}

// ---------------------------------------------------------------------------
// Account Detail
// ---------------------------------------------------------------------------
async function loadAccountStats(id) {
    try {
        const s = await api(`/api/accounts/${id}/stats`);
        setText("s-following", s.active_following);
        setText("s-backed", s.followed_back);
        setText("s-unfollowed", s.unfollowed);
        setText("s-today-f", s.today_followed);
        setText("s-ratio", s.followback_ratio + "%");
    } catch (_) {}
}

async function loadAccountActivity(id) {
    try {
        const data = await api(`/api/accounts/${id}/activity?limit=20`);
        const el = document.getElementById("account-activity");
        if (!el) return;
        if (!data.length) {
            el.innerHTML = '<p class="text-gray-600 text-sm">No activity yet</p>';
            return;
        }
        el.innerHTML = data.map((a) => activityItem(a, false)).join("");
    } catch (_) {}
}

let accountChart = null;
async function loadAccountChart(id) {
    try {
        const data = await api(`/api/accounts/${id}/daily-stats?days=14`);
        const labels = data.map((d) => d.day.slice(5));
        const follows = data.map((d) => d.follow || 0);
        const unfollows = data.map((d) => d.unfollow || 0);
        const likes = data.map((d) => d.like || 0);

        if (accountChart) accountChart.destroy();
        const ctx = document.getElementById("accountChart");
        if (!ctx) return;
        accountChart = new Chart(ctx, {
            type: "bar",
            data: {
                labels,
                datasets: [
                    { label: "Follows", data: follows, backgroundColor: "#22c55e" },
                    { label: "Unfollows", data: unfollows, backgroundColor: "#ef4444" },
                    { label: "Likes", data: likes, backgroundColor: "#eab308" },
                ],
            },
            options: chartOptions(),
        });
    } catch (_) {}
}

// ---------------------------------------------------------------------------
// Bot Controls
// ---------------------------------------------------------------------------
async function testAccount(id) {
    const btn = document.getElementById("btn-test");
    if (btn) { btn.textContent = "Testing..."; btn.disabled = true; }
    try {
        const res = await api(`/api/accounts/${id}/test`, { method: "POST" });
        showToast(res.message, res.success ? "success" : "error");
    } catch (e) {
        showToast(e.message, "error");
    } finally {
        if (btn) { btn.textContent = "Test Connection"; btn.disabled = false; }
    }
}

async function startBot(event, id) {
    event.preventDefault();
    const form = event.target;
    const data = {
        target_username: form.target_username.value,
        follow_count: parseInt(form.follow_count.value) || 100,
    };
    try {
        const res = await api(`/api/accounts/${id}/start`, {
            method: "POST",
            body: JSON.stringify(data),
        });
        showToast(res.message, res.success ? "success" : "error");
        if (res.success) setTimeout(() => location.reload(), 1000);
    } catch (e) {
        showToast(e.message, "error");
    }
    document.getElementById("start-modal").classList.add("hidden");
}

async function stopBot(id) {
    try {
        const res = await api(`/api/accounts/${id}/stop`, { method: "POST" });
        showToast(res.message, res.success ? "success" : "warning");
        setTimeout(() => location.reload(), 1000);
    } catch (e) {
        showToast(e.message, "error");
    }
}

// ---------------------------------------------------------------------------
// Account Management
// ---------------------------------------------------------------------------
async function addAccount(event) {
    event.preventDefault();
    const form = event.target;
    const data = {
        ig_username: form.ig_username.value,
        ig_password: form.ig_password.value,
        ig_2fa_seed: form.ig_2fa_seed.value,
        proxy_url: form.proxy_url.value,
    };
    try {
        const res = await api("/api/accounts", {
            method: "POST",
            body: JSON.stringify(data),
        });
        showToast(res.message, "success");
        setTimeout(() => location.reload(), 500);
    } catch (e) {
        showToast(e.message, "error");
    }
}

async function deleteAccount(id, username) {
    if (!confirm(`Delete @${username} and all its data?`)) return;
    try {
        await api(`/api/accounts/${id}`, { method: "DELETE" });
        showToast("Account deleted", "success");
        setTimeout(() => location.reload(), 500);
    } catch (e) {
        showToast(e.message, "error");
    }
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
async function updateConfig(accountId, key, value) {
    try {
        await api(`/api/accounts/${accountId}/config`, {
            method: "PUT",
            body: JSON.stringify({ [key]: value }),
        });
        showToast(`Updated ${key}`, "success");
    } catch (e) {
        showToast(e.message, "error");
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function activityItem(a, showAccount) {
    const colors = {
        follow: "text-green-400",
        unfollow: "text-red-400",
        like: "text-yellow-400",
        story_view: "text-purple-400",
        error: "text-red-500",
        action_block: "text-red-500",
        check_followback: "text-blue-400",
        withdraw_request: "text-orange-400",
    };
    const color = colors[a.action] || "text-gray-400";
    const time = a.created_at ? a.created_at.slice(11, 19) : "";
    const acct = showAccount && a.account_username ? `<span class="text-gray-600">@${a.account_username}</span> ` : "";
    return `<div class="flex items-start gap-2 py-1.5 border-b border-gray-800/50 last:border-0">
        <span class="text-xs text-gray-600 w-14 shrink-0">${time}</span>
        <div class="text-xs">
            ${acct}<span class="${color} font-medium">${a.action}</span>
            ${a.target_username ? `<span class="text-gray-400">@${a.target_username}</span>` : ""}
        </div>
    </div>`;
}

function chartOptions() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { labels: { color: "#9ca3af", boxWidth: 12, padding: 15, font: { size: 11 } } },
        },
        scales: {
            x: { ticks: { color: "#6b7280", font: { size: 10 } }, grid: { color: "#1f2937" } },
            y: { ticks: { color: "#6b7280", font: { size: 10 } }, grid: { color: "#1f2937" }, beginAtZero: true },
        },
    };
}
