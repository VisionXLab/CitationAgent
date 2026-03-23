// ==================== 论文列表管理 ====================
(function () {
    let _idCounter = 0;
    const STORAGE_KEY = 'citation_claw_papers';

    function _saveState() {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(window.getPaperGroups()));
        } catch (e) {}
    }

    // autoFocus=true 时聚焦（用户点+按钮），false 时静默恢复（从 localStorage 恢复）
    function _addAliasRow(item, initialText, autoFocus) {
        const aliasesEl = item.querySelector('.paper-aliases');
        const row = document.createElement('div');
        row.className = 'paper-alias-row';

        const prefix = document.createElement('span');
        prefix.className = 'paper-alias-prefix';
        prefix.textContent = '↳';

        const alias = document.createElement('span');
        alias.className = 'paper-alias';
        alias.contentEditable = 'true';
        if (initialText) alias.textContent = initialText;

        alias.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); alias.blur(); }
        });
        alias.addEventListener('blur', () => {
            if (!alias.textContent.trim()) row.remove();
            _saveState();
        });

        row.appendChild(prefix);
        row.appendChild(alias);
        aliasesEl.appendChild(row);
        if (autoFocus) alias.focus();
    }

    function _deletePaperItem(item) {
        item.remove();
        _saveState();
    }

    // savedAliases: 从 localStorage 恢复时传入的曾用名列表
    window.addPaper = function (title, savedAliases) {
        const id = ++_idCounter;
        const item = document.createElement('div');
        item.className = 'paper-item';
        item.dataset.id = id;

        const main = document.createElement('div');
        main.className = 'paper-item-main';

        const titleEl = document.createElement('span');
        titleEl.className = 'paper-item-title';
        titleEl.contentEditable = 'true';
        titleEl.textContent = title;
        titleEl.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                e.preventDefault();
                document.getElementById('paper-input').focus();
            }
        });
        titleEl.addEventListener('blur', () => {
            if (!titleEl.textContent.trim()) _deletePaperItem(item);
            else _saveState();
        });

        const actions = document.createElement('div');
        actions.className = 'paper-item-actions';

        const btnAlias = document.createElement('button');
        btnAlias.className = 'paper-btn-alias';
        btnAlias.title = '添加曾用名';
        btnAlias.textContent = '+';
        btnAlias.addEventListener('click', () => _addAliasRow(item, '', true));

        const btnDel = document.createElement('button');
        btnDel.className = 'paper-btn-delete';
        btnDel.title = '删除';
        btnDel.innerHTML = '<i class="bi bi-trash"></i>';
        btnDel.addEventListener('click', () => _deletePaperItem(item));

        actions.appendChild(btnAlias);
        actions.appendChild(btnDel);
        main.appendChild(titleEl);
        main.appendChild(actions);

        const aliasesEl = document.createElement('div');
        aliasesEl.className = 'paper-aliases';

        item.appendChild(main);
        item.appendChild(aliasesEl);
        document.getElementById('paper-list').appendChild(item);

        // 恢复已保存的曾用名（不自动聚焦）
        if (savedAliases && savedAliases.length) {
            savedAliases.forEach(a => { if (a.trim()) _addAliasRow(item, a, false); });
        }
        _saveState();
    };

    window.getPaperGroups = function () {
        const groups = [];
        document.querySelectorAll('.paper-item').forEach(item => {
            const title = item.querySelector('.paper-item-title').textContent.trim();
            if (!title) return;
            const aliases = [];
            item.querySelectorAll('.paper-alias').forEach(a => {
                const t = a.textContent.trim();
                if (t) aliases.push(t);
            });
            groups.push({ title, aliases });
        });
        return groups;
    };

    // 从 localStorage 恢复论文列表（在页面加载时调用）
    window.restorePaperList = function () {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            if (!saved) return;
            const groups = JSON.parse(saved);
            if (!Array.isArray(groups)) return;
            groups.forEach(g => {
                if (g && g.title) window.addPaper(g.title, g.aliases || []);
            });
        } catch (e) {}
    };
})();

// ==================== SPA Router ====================
var SpaRouter = (function () {
    let _currentPanel = 'home';
    let _configLoaded = false;

    function switchTo(name) {
        // Hide all panels (both class and inline style for cache-safety)
        document.querySelectorAll('.spa-panel').forEach(p => {
            p.classList.remove('spa-panel-active');
            p.style.display = 'none';
        });
        // Show target
        const target = document.getElementById('spa-panel-' + name);
        if (target) {
            target.classList.add('spa-panel-active');
            target.style.display = '';
        }
        // Update navbar active state
        document.querySelectorAll('[data-spa-panel]').forEach(link => {
            link.classList.toggle('active', link.dataset.spaPanel === name);
        });
        _currentPanel = name;

        // Lazy-load data for panels
        if (name === 'config' && !_configLoaded) {
            _configLoaded = true;
            loadConfig();
            checkExistingFiles();
        } else if (name === 'config') {
            // Refresh config each time we switch to it
            loadConfig();
        }
        if (name === 'results') {
            loadResults();
        }

        // Update URL without reload
        var url = new URL(window.location);
        if (name === 'home') {
            url.searchParams.delete('panel');
        } else {
            url.searchParams.set('panel', name);
        }
        history.replaceState(null, '', url);
    }

    function init() {
        // Bind all data-spa-panel links (navbar + inline links)
        document.addEventListener('click', function (e) {
            var link = e.target.closest('[data-spa-panel]');
            if (link) {
                e.preventDefault();
                switchTo(link.dataset.spaPanel);
            }
        });

        // Check URL for ?panel= parameter
        var params = new URLSearchParams(window.location.search);
        var panel = params.get('panel');
        if (panel && document.getElementById('spa-panel-' + panel)) {
            switchTo(panel);
        } else {
            switchTo('home');
        }
    }

    return { init: init, switchTo: switchTo, current: function () { return _currentPanel; } };
})();

// ==================== Global Progress Bar ====================
var GlobalProgress = (function () {
    function show(label, pct) {
        var el = document.getElementById('global-progress');
        if (!el) return;
        el.classList.add('active');
        if (label) {
            var lbl = document.getElementById('global-progress-label');
            if (lbl) lbl.textContent = label;
        }
        if (pct !== undefined) {
            update(pct);
        }
    }

    function update(pct) {
        var bar = document.getElementById('global-progress-bar');
        var pctEl = document.getElementById('global-progress-pct');
        if (bar) bar.style.width = pct + '%';
        if (pctEl) pctEl.textContent = pct > 0 ? (pct + '%') : '';
    }

    function setLabel(label) {
        var lbl = document.getElementById('global-progress-label');
        if (lbl) lbl.textContent = label;
    }

    function hide() {
        var el = document.getElementById('global-progress');
        if (el) el.classList.remove('active');
    }

    function init() {
        var el = document.getElementById('global-progress');
        if (el) {
            el.addEventListener('click', function () {
                SpaRouter.switchTo('home');
                var log = document.getElementById('idx-log-section');
                if (log) log.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            });
        }
    }

    return { show: show, update: update, setLabel: setLabel, hide: hide, init: init };
})();

// ==================== Shared Utility ====================
function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// ==================== Config Panel Functions (module scope) ====================
async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        const config = await response.json();

        const el = id => document.getElementById(id);

        // Config panel fields
        if (el('scraper-api-keys')) el('scraper-api-keys').value = (config.scraper_api_keys || []).join(',');
        if (el('openai-api-key')) el('openai-api-key').value = config.openai_api_key || '';
        if (el('openai-base-url')) el('openai-base-url').value = config.openai_base_url || '';
        if (el('openai-model')) el('openai-model').value = config.openai_model || '';
        if (el('output-prefix')) el('output-prefix').value = config.default_output_prefix || 'paper';
        if (el('sleep-between-pages')) el('sleep-between-pages').value = config.sleep_between_pages || 10;
        if (el('parallel-author-search')) el('parallel-author-search').value = config.parallel_author_search || 10;
        if (el('resume-page')) el('resume-page').value = config.resume_page_count || 0;
        // enable_year_traverse is intentionally NOT loaded from config — always starts unchecked each session
        if (el('debug-mode')) el('debug-mode').checked = config.debug_mode || false;
        if (el('test-mode')) el('test-mode').checked = config.test_mode || false;
        if (el('retry-max-attempts')) el('retry-max-attempts').value = config.retry_max_attempts || 3;
        if (el('retry-intervals')) el('retry-intervals').value = config.retry_intervals || '5,10,20';
        if (el('scraper-premium')) el('scraper-premium').checked = config.scraper_premium || false;
        if (el('scraper-ultra-premium')) el('scraper-ultra-premium').checked = config.scraper_ultra_premium || false;
        if (el('scraper-session')) el('scraper-session').checked = config.scraper_session || false;
        if (el('scholar-no-filter')) el('scholar-no-filter').checked = config.scholar_no_filter || false;
        if (el('scraper-geo-rotate')) el('scraper-geo-rotate').checked = config.scraper_geo_rotate || false;
        if (el('author-search-prompt1')) el('author-search-prompt1').value = config.author_search_prompt1 || '';
        if (el('author-search-prompt2')) el('author-search-prompt2').value = config.author_search_prompt2 || '';
        if (el('enable-renowned-scholar')) el('enable-renowned-scholar').checked = config.enable_renowned_scholar_filter || false;
        if (el('renowned-scholar-model')) el('renowned-scholar-model').value = config.renowned_scholar_model || 'gemini-3-flash-preview-nothinking';
        if (el('renowned-scholar-prompt')) el('renowned-scholar-prompt').value = config.renowned_scholar_prompt || '';
        if (el('enable-author-verification')) el('enable-author-verification').checked = config.enable_author_verification || false;
        if (el('author-verify-model')) el('author-verify-model').value = config.author_verify_model || 'gemini-3-pro-preview-search';
        if (el('author-verify-prompt')) el('author-verify-prompt').value = config.author_verify_prompt || '';
        if (el('api-access-token')) el('api-access-token').value = config.api_access_token || '';
        if (el('api-user-id')) el('api-user-id').value = config.api_user_id || '';

        // Toggle visibility for sub-config sections
        var renownedScholarConfig = el('renowned-scholar-config');
        if (renownedScholarConfig) {
            renownedScholarConfig.style.display = config.enable_renowned_scholar_filter ? 'block' : 'none';
        }
        var authorVerificationConfig = el('author-verification-config');
        if (authorVerificationConfig) {
            authorVerificationConfig.style.display = config.enable_author_verification ? 'block' : 'none';
        }
    } catch (error) {
        console.error('加载配置失败:', error);
    }
}

async function checkExistingFiles() {
    try {
        const response = await fetch('/api/results/list');
        const files = await response.json();

        const jsonlFiles = files.filter(f => f.type === '.jsonl');

        if (jsonlFiles.length > 0) {
            const alertDiv = document.getElementById('existing-files-alert');
            const listDiv = document.getElementById('existing-files-list');
            if (!alertDiv || !listDiv) return;

            jsonlFiles.sort((a, b) => b.modified - a.modified);

            const recentFiles = jsonlFiles.slice(0, 5);
            listDiv.innerHTML = '<strong>最近的结果文件:</strong><ul class="mb-0 mt-1">' +
                recentFiles.map(f => {
                    const date = new Date(f.modified * 1000).toLocaleString('zh-CN');
                    const size = (f.size / 1024).toFixed(1);
                    return `<li><code>${f.name}</code> (${size} KB, ${date})</li>`;
                }).join('') +
                '</ul>';

            if (jsonlFiles.length > 5) {
                listDiv.innerHTML += `<div class="mt-1 text-muted">...还有 ${jsonlFiles.length - 5} 个文件</div>`;
            }

            alertDiv.style.display = 'block';
        }
    } catch (error) {
        console.error('检查已有文件失败:', error);
    }
}

async function loadResults() {
    await resultsShowFolders();
}

function _resultsSetLoading(show) {
    document.getElementById('loading-indicator').style.display = show ? 'block' : 'none';
}
function _resultsShowView(view) {
    // view: 'empty' | 'folders' | 'files'
    document.getElementById('empty-state').style.display = view === 'empty' ? 'block' : 'none';
    document.getElementById('results-folder-view').style.display = view === 'folders' ? 'block' : 'none';
    document.getElementById('results-file-view').style.display = view === 'files' ? 'block' : 'none';
    document.getElementById('results-back-btn').style.display = view === 'files' ? 'inline-flex' : 'none';
    document.getElementById('results-panel-title').textContent = view === 'files'
        ? (window._resultCurrentFolderDisplay || '文件夹内容')
        : '结果文件夹';
}

async function resultsShowFolders() {
    _resultsSetLoading(true);
    _resultsShowView('empty');
    try {
        const res = await fetch('/api/results/folders');
        const folders = await res.json();
        _resultsSetLoading(false);
        if (folders.length === 0) {
            _resultsShowView('empty');
            return;
        }
        const list = document.getElementById('results-folder-list');
        list.innerHTML = '';
        folders.forEach(folder => {
            const date = new Date(folder.modified * 1000).toLocaleString('zh-CN');
            const sizeMB = (folder.size / 1024 / 1024).toFixed(2);
            const item = document.createElement('div');
            item.className = 'list-group-item d-flex align-items-center gap-3 py-3';
            item.innerHTML = `
                <i class="bi bi-folder2 fs-4 text-warning flex-shrink-0"></i>
                <div class="flex-grow-1 min-width-0" style="cursor:pointer" data-folder="${folder.name}" data-display="${folder.display_name}">
                    <div class="fw-semibold text-truncate">${folder.display_name}</div>
                    <small class="text-muted">${folder.file_count} 个文件 &nbsp;·&nbsp; ${sizeMB} MB &nbsp;·&nbsp; ${date}</small>
                </div>
                <i class="bi bi-chevron-right text-muted flex-shrink-0" style="cursor:pointer" data-folder="${folder.name}" data-display="${folder.display_name}"></i>
                <button class="btn btn-sm btn-outline-danger flex-shrink-0 ms-1" data-delete="${folder.name}" title="删除此文件夹">
                    <i class="bi bi-trash"></i>
                </button>
            `;
            item.querySelector('[data-folder]').addEventListener('click', (e) => {
                const el = e.currentTarget;
                resultsOpenFolder(el.dataset.folder, el.dataset.display);
            });
            item.querySelector('[data-delete]').addEventListener('click', async (e) => {
                e.stopPropagation();
                const name = e.currentTarget.dataset.delete;
                if (!confirm(`确定要删除文件夹 "${name}" 及其所有文件吗？此操作不可撤销。`)) return;
                try {
                    const r = await fetch(`/api/results/folder/${encodeURIComponent(name)}`, { method: 'DELETE' });
                    if (!r.ok) throw new Error(await r.text());
                    await resultsShowFolders();
                } catch (err) {
                    alert('删除失败：' + err.message);
                }
            });
            list.appendChild(item);
        });
        _resultsShowView('folders');
    } catch (err) {
        console.error('加载文件夹失败:', err);
        _resultsSetLoading(false);
        _resultsShowView('empty');
    }
}

async function resultsOpenFolder(folderName, displayName) {
    window._resultCurrentFolderDisplay = displayName;
    _resultsSetLoading(true);
    _resultsShowView('empty');
    try {
        const res = await fetch(`/api/results/list?folder=${encodeURIComponent(folderName)}`);
        const files = await res.json();
        _resultsSetLoading(false);
        const table = document.getElementById('results-table');
        table.innerHTML = '';
        files.forEach(file => {
            const typeClass = file.type === '.xlsx' ? 'success' :
                              file.type === '.json' ? 'info' :
                              file.type === '.html' ? 'primary' : 'warning';
            const size = (file.size / 1024).toFixed(2);
            const date = new Date(file.modified * 1000).toLocaleString('zh-CN');
            const icon = file.type === '.xlsx' ? 'excel' :
                         file.type === '.html' ? 'richtext' : 'code';
            const actionBtn = file.type === '.html'
                ? `<a href="/api/results/view/${file.path}" target="_blank" class="btn btn-sm btn-primary">
                       <i class="bi bi-eye"></i> 查看报告
                   </a>`
                : `<a href="/api/results/download/${file.path}" class="btn btn-sm btn-outline-primary" download>
                       <i class="bi bi-download"></i> 下载
                   </a>`;
            const row = document.createElement('tr');
            row.innerHTML = `
                <td><i class="bi bi-file-earmark-${icon}"></i> ${file.name}</td>
                <td><span class="badge bg-${typeClass}">${file.type}</span></td>
                <td>${size} KB</td>
                <td>${date}</td>
                <td>${actionBtn}</td>
            `;
            table.appendChild(row);
        });
        document.getElementById('file-count').textContent = files.length;
        _resultsShowView('files');
    } catch (err) {
        console.error('加载文件夹内容失败:', err);
        _resultsSetLoading(false);
        _resultsShowView('empty');
    }
}

function collectConfig() {
    return {
        scraper_api_keys: document.getElementById('scraper-api-keys').value
            .split(',').map(k => k.trim()).filter(k => k),
        openai_api_key: document.getElementById('openai-api-key').value,
        openai_base_url: document.getElementById('openai-base-url').value,
        openai_model: document.getElementById('openai-model').value,
        default_output_prefix: document.getElementById('output-prefix').value,
        sleep_between_pages: parseInt(document.getElementById('sleep-between-pages').value) || 10,
        parallel_author_search: parseInt(document.getElementById('parallel-author-search').value) || 10,
        resume_page_count: parseInt(document.getElementById('resume-page').value) || 0,
        enable_year_traverse: document.getElementById('enable-year-traverse')?.checked ?? false,
        debug_mode: document.getElementById('debug-mode').checked,
        test_mode: document.getElementById('test-mode').checked,
        retry_max_attempts: parseInt(document.getElementById('retry-max-attempts').value) || 3,
        retry_intervals: document.getElementById('retry-intervals').value || '5,10,20',
        scraper_premium: document.getElementById('scraper-premium').checked,
        scraper_ultra_premium: document.getElementById('scraper-ultra-premium').checked,
        scraper_session: document.getElementById('scraper-session').checked,
        scholar_no_filter: document.getElementById('scholar-no-filter').checked,
        scraper_geo_rotate: document.getElementById('scraper-geo-rotate').checked,
        author_search_prompt1: document.getElementById('author-search-prompt1').value,
        author_search_prompt2: document.getElementById('author-search-prompt2').value,
        enable_renowned_scholar_filter: document.getElementById('enable-renowned-scholar').checked,
        renowned_scholar_model: document.getElementById('renowned-scholar-model').value,
        renowned_scholar_prompt: document.getElementById('renowned-scholar-prompt').value,
        enable_author_verification: document.getElementById('enable-author-verification').checked,
        author_verify_model: document.getElementById('author-verify-model').value,
        author_verify_prompt: document.getElementById('author-verify-prompt').value,
        api_access_token: document.getElementById('api-access-token').value,
        api_user_id: document.getElementById('api-user-id').value
    };
}

async function saveConfigNow() {
    const config = collectConfig();
    const response = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    });
    return await response.json();
}

// ==================== 新首页逻辑（自动化流水线）====================
function initIndexPage() {
    const runBtn = document.getElementById('idx-run-btn');
    if (!runBtn) return;

    // 恢复上次保存的论文列表（页面切换后不丢失）
    if (window.restorePaperList) window.restorePaperList();

    // 论文输入框：按 Enter 添加条目
    const paperInput = document.getElementById('paper-input');
    if (paperInput) {
        paperInput.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const title = paperInput.value.trim();
                if (title) {
                    addPaper(title);
                    paperInput.value = '';
                }
                paperInput.focus();
            }
        });
    }

    // 初始化 WebSocket
    const ws = new WebSocketManager();
    ws.connect();

    // API Key 输入框：有内容时切换为 password 类型（遮盖），空时切回 text（显示 placeholder）
    function _syncApiKeyType(input) {
        input.type = input.value ? 'password' : 'text';
    }
    document.getElementById('idx-openai-key').addEventListener('input', function () {
        _syncApiKeyType(this);
    });

    // Phase label 映射
    const phaseLabels = {
        'URL': 'Phase 0 · 查找引用链接',
        'Phase 1': 'Phase 1 · 爬取引用列表',
        'Phase 2': 'Phase 2 · 搜索学者信息',
        'Phase 3': 'Phase 3 · 导出结果',
        'Phase 4': 'Phase 4 · 搜索引用描述',
        'Phase 5': 'Phase 5 · 生成分析报告',
    };
    let currentPhase = '处理中...';

    // 加载配置并填充 Home 面板表单
    (async () => {
        try {
            const resp = await fetch('/api/config');
            const cfg = await resp.json();
            const el = id => document.getElementById(id);
            el('idx-scraper-keys').value = (cfg.scraper_api_keys || []).join(',');
            el('idx-openai-key').value = cfg.openai_api_key || '';
            _syncApiKeyType(el('idx-openai-key'));
            el('idx-openai-url').value = cfg.openai_base_url || '';
            el('idx-openai-model').value = cfg.openai_model || '';
            el('idx-output-prefix').value = cfg.default_output_prefix || 'paper';
            el('idx-renowned-scholar').checked = cfg.enable_renowned_scholar_filter !== false;
            el('idx-author-verify').checked = cfg.enable_author_verification || false;
            el('idx-dashboard').checked = cfg.enable_dashboard !== false;
            el('idx-service-tier').value = cfg.service_tier || 'basic';
            el('idx-dashboard-model').value = cfg.dashboard_model || 'gemini-3-flash-preview-nothinking';
            el('idx-api-access-token').value = cfg.api_access_token || '';
            el('idx-api-user-id').value = cfg.api_user_id || '';
        } catch (e) {
            console.error('加载配置失败:', e);
        }
    })();

    // 保存配置按钮
    document.getElementById('idx-save-config-btn').addEventListener('click', async () => {
        await saveIndexConfig();
    });

    async function saveIndexConfig() {
        const el = id => document.getElementById(id);
        const keys = el('idx-scraper-keys').value.split(',').map(k => k.trim()).filter(k => k);
        const body = {
            scraper_api_keys: keys,
            openai_api_key: el('idx-openai-key').value,
            openai_base_url: el('idx-openai-url').value,
            openai_model: el('idx-openai-model').value,
            default_output_prefix: el('idx-output-prefix').value,
            enable_renowned_scholar_filter: el('idx-renowned-scholar').checked,
            enable_author_verification: el('idx-author-verify').checked,
            enable_dashboard: el('idx-dashboard').checked,
            service_tier: el('idx-service-tier').value,
            skip_author_search: false,
            // Derive citing-description settings directly from tier to ensure consistency
            ...({
                basic:    { enable_citing_description: false, citing_description_scope: 'all',           dashboard_skip_citing_analysis: true  },
                advanced: { enable_citing_description: true,  citing_description_scope: 'renowned_only', dashboard_skip_citing_analysis: false },
                full:     { enable_citing_description: true,  citing_description_scope: 'all',           dashboard_skip_citing_analysis: false },
            }[el('idx-service-tier').value]),
            dashboard_model: el('idx-dashboard-model').value,
            api_access_token: el('idx-api-access-token').value,
            api_user_id: el('idx-api-user-id').value,
        };
        try {
            const cfgResp = await fetch('/api/config');
            const existing = await cfgResp.json();
            // 费用追踪字段：空值不覆盖已有配置
            if (!body.api_access_token && existing.api_access_token) delete body.api_access_token;
            if (!body.api_user_id && existing.api_user_id) delete body.api_user_id;
            const merged = Object.assign({}, existing, body);
            const resp = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(merged)
            });
            const data = await resp.json();
            if (data.status === 'success') {
                const ind = document.getElementById('idx-save-indicator');
                ind.style.opacity = '1';
                setTimeout(() => { ind.style.opacity = '0'; }, 2000);
            }
        } catch (e) {
            console.error('保存配置失败:', e);
        }
    }

    // ─── Service Tier Preset Logic ───
    const tierSelect = document.getElementById('idx-service-tier');
    let PRESETS = {};
    (async () => {
        try {
            const resp = await fetch('/api/presets');
            PRESETS = await resp.json();
        } catch (e) { console.error('Failed to load presets:', e); }
    })();

    tierSelect.addEventListener('change', () => {
        const tier = tierSelect.value;
        const preset = PRESETS[tier];
        if (!preset) return;
        const sw = preset.switches;
        document.getElementById('idx-renowned-scholar').checked = sw.enable_renowned_scholar_filter;
        document.getElementById('idx-dashboard').checked = sw.enable_dashboard;
    });

    // WebSocket 事件监听
    ws.on('log', log => {
        appendIndexLog(log);
        // Show global progress on any log activity
        GlobalProgress.show(currentPhase);
    });
    ws.on('history', logs => logs.forEach(log => appendIndexLog(log)));
    ws.on('progress', progress => {
        updateIndexProgress(progress);
        // Update global progress bar
        GlobalProgress.show(currentPhase, progress.percentage || 0);
    });
    ws.on('all_done', data => {
        stopRunTimer();
        showIndexResults(data);
        // Hide global progress after 3 seconds
        setTimeout(() => { GlobalProgress.hide(); }, 3000);
    });

    ws.on('year_traverse_prompt', data => {
        document.getElementById('yt-citation-count').textContent =
            (data.citation_count || 0).toLocaleString();
        const ytModal = new bootstrap.Modal(document.getElementById('yearTraverseModal'));
        ytModal.show();

        document.getElementById('yt-btn-enable').onclick = async () => {
            ytModal.hide();
            const ytToggle = document.getElementById('enable-year-traverse');
            if (ytToggle) ytToggle.checked = true;
            try {
                await fetch('/api/task/year-traverse-respond', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enable: true })
                });
            } catch (e) { console.error('year-traverse-respond failed', e); }
        };

        document.getElementById('yt-btn-skip').onclick = async () => {
            ytModal.hide();
            try {
                await fetch('/api/task/year-traverse-respond', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enable: false })
                });
            } catch (e) { console.error('year-traverse-respond failed', e); }
        };
    });

    ws.on('quota_exceeded', data => {
        const msgEl = document.getElementById('quota-exceeded-message');
        if (msgEl && data.message) {
            msgEl.textContent = data.message;
        }
        GlobalProgress.hide();
        stopRunTimer();
        const modal = new bootstrap.Modal(document.getElementById('quotaExceededModal'));
        modal.show();
    });

    // 开始分析按钮
    runBtn.addEventListener('click', async () => {
        // 先把输入框里未提交的内容也加进列表
        const paperInput = document.getElementById('paper-input');
        if (paperInput && paperInput.value.trim()) {
            addPaper(paperInput.value.trim());
            paperInput.value = '';
        }
        // API Key 检查
        if (window.checkApiKeysAndAlert && !window.checkApiKeysAndAlert(true, true)) return;
        const groups = getPaperGroups();
        if (groups.length === 0) {
            alert('请输入至少一篇论文题目');
            return;
        }
        await saveIndexConfig();

        // 预检查 LLM 余额
        try {
            var quotaResp = await fetch('/api/quota/check');
            var quotaData = await quotaResp.json();
            if (quotaData.configured && quotaData.remaining !== undefined) {
                appendIndexLog({
                    timestamp: new Date().toISOString(),
                    level: 'INFO',
                    message: '📊 LLM 当前余额: ' + quotaData.remaining + ' 实际额度 (≈ ¥' + quotaData.remaining_rmb + ')'
                });
            } else if (quotaData.configured && quotaData.error) {
                appendIndexLog({
                    timestamp: new Date().toISOString(),
                    level: 'WARNING',
                    message: '📊 LLM 余额查询失败: ' + quotaData.error
                });
            }
        } catch (e) {}

        const outputPrefix = document.getElementById('idx-output-prefix').value || 'paper';

        runBtn.disabled = true;
        runBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" style="animation:spin .8s linear infinite"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2.5" stroke-dasharray="40" stroke-dashoffset="10"/></svg>&nbsp; 运行中...';

        document.getElementById('idx-cancel-btn').style.display = 'inline-flex';
        document.getElementById('idx-progress-section').style.display = 'block';
        document.getElementById('idx-log-section').style.display = 'block';
        document.getElementById('idx-results-section').style.display = 'none';
        startRunTimer();

        // 清空日志，显示 empty placeholder
        document.getElementById('idx-log-container').innerHTML =
            '<div class="reasoning-empty"><div class="reasoning-empty-icon">🤖</div><div class="reasoning-empty-text">智能体正在初始化...</div></div>';

        // 立即显示当前搜索模型
        const _modelEl = document.getElementById('idx-openai-model');
        appendIndexLog({
            timestamp: new Date().toLocaleString('zh-CN'),
            level: 'INFO',
            message: '🔍 搜索模型：' + (_modelEl ? _modelEl.value || '(未设置)' : '(未设置)')
        });

        // 显示 thinking indicator
        const thinking = document.getElementById('rp-thinking-indicator');
        if (thinking) thinking.classList.add('active');

        // 重置进度
        updateIndexProgress({ percentage: 0, current: 0, total: 0 });
        currentPhase = '初始化中...';
        document.getElementById('idx-phase-label').textContent = currentPhase;

        // Show global progress bar
        GlobalProgress.show('初始化中...', 0);

        try {
            const resp = await fetch('/api/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ papers: groups, output_prefix: outputPrefix })
            });
            const data = await resp.json();
            if (data.status !== 'success') {
                alert('启动失败: ' + data.message);
                resetRunBtn();
            }
        } catch (e) {
            console.error('启动失败:', e);
            alert('启动失败，请检查控制台');
            resetRunBtn();
        }
    });

    // 取消按钮
    document.getElementById('idx-cancel-btn').addEventListener('click', async () => {
        if (!confirm('确定要取消当前任务吗？')) return;
        try {
            await fetch('/api/task/cancel', { method: 'POST' });
        } catch (e) {
            console.error('取消失败:', e);
        }
        resetRunBtn();
        GlobalProgress.hide();
    });

    // 清空日志
    document.getElementById('idx-clear-log-btn').addEventListener('click', () => {
        document.getElementById('idx-log-container').innerHTML =
            '<div class="reasoning-empty"><div class="reasoning-empty-icon">🧹</div><div class="reasoning-empty-text">日志已清空</div></div>';
    });

    let _runTimer = null;
    let _runStart  = 0;

    function startRunTimer() {
        _runStart = Date.now();
        const bar = document.getElementById('running-heartbeat');
        const msg = document.getElementById('hb-msg');
        if (bar) bar.style.display = 'flex';
        if (msg) msg.textContent = '还在运行中，请耐心等待！';
        if (_runTimer) clearInterval(_runTimer);
        _runTimer = setInterval(() => {
            const elapsed = Math.floor((Date.now() - _runStart) / 1000);
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            const timeStr = mins > 0 ? `${mins}分${secs}秒` : `${secs}秒`;
            if (msg) msg.textContent = `还在运行中，已运行时长为${timeStr}，请耐心等待！`;
        }, 1000);
    }

    function stopRunTimer() {
        if (_runTimer) { clearInterval(_runTimer); _runTimer = null; }
        const bar = document.getElementById('running-heartbeat');
        if (bar) bar.style.display = 'none';
    }

    function resetRunBtn() {
        runBtn.disabled = false;
        runBtn.innerHTML = '<i class="bi bi-play-fill"></i> 开始分析';
        document.getElementById('idx-cancel-btn').style.display = 'none';
        resetCacheRunBtn();
        const thinking = document.getElementById('rp-thinking-indicator');
        if (thinking) thinking.classList.remove('active');
        stopRunTimer();
    }

    // 从缓存生成报告按钮
    const cacheRunBtn = document.getElementById('idx-cache-run-btn');
    if (cacheRunBtn) {
        cacheRunBtn.addEventListener('click', async () => {
            const titleInput = document.getElementById('idx-cache-title');
            const paperTitle = titleInput ? titleInput.value.trim() : '';
            if (!paperTitle) {
                alert('请输入论文标题');
                return;
            }

            cacheRunBtn.disabled = true;
            cacheRunBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" style="animation:spin .8s linear infinite"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2.5" stroke-dasharray="40" stroke-dashoffset="10"/></svg>&nbsp; 生成中...';

            document.getElementById('idx-progress-section').style.display = 'block';
            document.getElementById('idx-log-section').style.display = 'block';
            document.getElementById('idx-log-container').innerHTML = '';

            try {
                const resp = await fetch('/api/run/from-cache', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ paper_title: paperTitle, output_prefix: 'cached' })
                });
                const data = await resp.json();
                if (!resp.ok) {
                    alert(data.message || '启动失败');
                    cacheRunBtn.disabled = false;
                    cacheRunBtn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> 生成报告';
                    return;
                }
                // WS already connected; progress/logs will stream automatically
            } catch (e) {
                alert('请求失败: ' + e.message);
                cacheRunBtn.disabled = false;
                cacheRunBtn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> 生成报告';
            }
        });
    }

    function resetCacheRunBtn() {
        if (cacheRunBtn) {
            cacheRunBtn.disabled = false;
            cacheRunBtn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> 生成报告';
        }
    }

    // 检测当前 phase
    function detectPhase(msg) {
        if (!msg) return;
        if (msg.includes('Phase 5') || msg.includes('画像报告')) {
            currentPhase = phaseLabels['Phase 5'];
        } else if (msg.includes('Phase 4') || msg.includes('引用描述')) {
            currentPhase = phaseLabels['Phase 4'];
        } else if (msg.includes('Phase 3') || msg.includes('导出结果')) {
            currentPhase = phaseLabels['Phase 3'];
        } else if (msg.includes('Phase 2') || msg.includes('作者信息') || msg.includes('作者学术')) {
            currentPhase = phaseLabels['Phase 2'];
        } else if (msg.includes('Phase 1') || msg.includes('爬取引用') || msg.includes('抓取')) {
            currentPhase = phaseLabels['Phase 1'];
        } else if (msg.includes('URL') || msg.includes('引用链接') || msg.includes('citation_url')) {
            currentPhase = phaseLabels['URL'];
        }
        const lbl = document.getElementById('idx-phase-label');
        if (lbl) lbl.textContent = currentPhase;
        // Also update global progress label
        GlobalProgress.setLabel(currentPhase);
    }

    function appendIndexLog(log) {
        const container = document.getElementById('idx-log-container');
        // Clear empty placeholder
        const empty = container.querySelector('.reasoning-empty');
        if (empty) container.innerHTML = '';

        const level = (log.level || 'INFO').toUpperCase();
        const msg = (log.message || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        const ts = (log.timestamp || '').replace(/^\d{4}-\d{2}-\d{2}\s/, ''); // keep only time

        // Detect phase from message
        detectPhase(log.message || '');

        const entry = document.createElement('div');
        entry.className = 're-entry';

        let levelClass = 're-badge-info';
        let dotClass   = 're-dot-info';
        let msgClass   = 're-msg-info';
        let badge      = 'INFO';

        if (level === 'SUCCESS') {
            levelClass = 're-badge-success'; dotClass = 're-dot-success'; msgClass = 're-msg-success'; badge = 'DONE';
        } else if (level === 'WARNING') {
            levelClass = 're-badge-warning'; dotClass = 're-dot-warning'; msgClass = 're-msg-warning'; badge = 'WARN';
        } else if (level === 'ERROR') {
            levelClass = 're-badge-error'; dotClass = 're-dot-error'; msgClass = 're-msg-error'; badge = 'ERR';
        }

        entry.innerHTML =
            `<div class="re-dot ${dotClass}"></div>` +
            `<span class="re-ts">${ts}</span>` +
            `<span class="re-badge ${levelClass}">${badge}</span>` +
            `<span class="re-msg ${msgClass}">${msg}</span>`;

        container.appendChild(entry);
        container.scrollTop = container.scrollHeight;

        if (log.message && log.message.includes('全部完成')) {
            resetRunBtn();
        }
    }

    function updateIndexProgress(progress) {
        const bar  = document.getElementById('idx-progress-bar');
        const text = document.getElementById('idx-progress-text');
        if (bar) bar.style.width = (progress.percentage || 0) + '%';
        if (text && progress.total > 0) {
            text.textContent = `${progress.current} / ${progress.total}`;
        }
    }

    // 规范化后端返回的文件路径：反斜杠→正斜杠
    function normPath(s) { return s ? s.replace(/\\/g, '/') : ''; }

    async function showIndexResults(data) {
        resetRunBtn();
        const section = document.getElementById('idx-results-section');
        const body    = document.getElementById('idx-results-body');
        section.style.display = 'block';

        let html = '';

        if (data && data.excel) {
            const path = normPath(data.excel);
            const name = path.split('/').pop();
            html += `<div class="result-file-row">
                <span class="result-file-icon">📊</span>
                <span class="result-file-name">${name}</span>
                <a href="/api/results/download/${path}" class="btn-download btn-dl-excel" download>
                    <i class="bi bi-download"></i> Excel
                </a>
            </div>`;
        }
        if (data && data.json) {
            const path = normPath(data.json);
            const name = path.split('/').pop();
            html += `<div class="result-file-row">
                <span class="result-file-icon">📋</span>
                <span class="result-file-name">${name}</span>
                <a href="/api/results/download/${path}" class="btn-download btn-dl-json" download>
                    <i class="bi bi-download"></i> JSON
                </a>
            </div>`;
        }
        if (data && data.dashboard) {
            const path = normPath(data.dashboard);
            const name = path.split('/').pop();
            html += `<div class="dashboard-cta">
                <span class="result-file-icon">🔭</span>
                <div class="dashboard-cta-text">
                    <strong style="color:#bc8cff">多维画像分析报告已生成</strong><br>
                    <span style="font-size:11.5px">${name}</span>
                </div>
                <a href="/api/results/view/${path}" target="_blank" class="btn-download btn-dl-report">
                    <i class="bi bi-eye"></i> 查看报告
                </a>
            </div>`;
        }

        // 费用摘要卡片
        if (data && data.cost_summary) {
            var cs = data.cost_summary;
            var costRows = '';
            costRows += '<tr><td>ScraperAPI 消耗积分</td><td>' + cs.scraper_credits + ' credits</td></tr>';
            costRows += '<tr><td>ScraperAPI 请求次数</td><td>' + cs.scraper_requests + ' 次</td></tr>';
            costRows += '<tr><td>ScraperAPI 估算费用</td><td>$' + cs.scraper_cost_usd.toFixed(4) + ' <span style="font-size:10px;color:var(--light)">(按 $49/100k credits)</span></td></tr>';
            if (cs.llm_tracked) {
                costRows += '<tr><td>LLM API 消耗额度</td><td>' + cs.llm_quota_consumed.toFixed(4) + ' 实际额度 ≈ ¥' + cs.llm_cost_rmb.toFixed(2) + '</td></tr>';
                costRows += '<tr><td>LLM API 剩余额度</td><td>' + cs.llm_remaining.toFixed(2) + ' 实际额度 ≈ ¥' + cs.llm_remaining_rmb.toFixed(2) + '</td></tr>';
            }
            html += '<div class="cost-summary-card">'
                + '<div class="cost-summary-header"><i class="bi bi-coin"></i> 本次运行费用摘要</div>'
                + '<table class="cost-summary-table"><tbody>' + costRows + '</tbody></table>';
            if (cs.llm_tracked) {
                html += '<div class="cost-summary-note">⚠️ LLM 额度通过运行前后差值计算（API 限制），可能包含同时段其他消耗。1 实际额度 = 2 RMB（默认 api.gpt.ge 计价）。</div>';
            } else {
                html += '<div class="cost-summary-note">💡 配置「系统令牌」和「用户ID」后可追踪 LLM API 额度消耗。在 API 中转站个人中心获取。</div>';
            }
            html += '</div>';
        }

        // Fallback
        if (!html) {
            try {
                const resp = await fetch('/api/results/list');
                const files = await resp.json();
                files.filter(f => f.type === '.xlsx' || f.type === '.json').slice(0, 2).forEach(f => {
                    const isExcel = f.type === '.xlsx';
                    html += `<div class="result-file-row">
                        <span class="result-file-icon">${isExcel ? '📊' : '📋'}</span>
                        <span class="result-file-name">${f.name}</span>
                        <a href="/api/results/download/${encodeURIComponent(f.name)}"
                           class="btn-download ${isExcel ? 'btn-dl-excel' : 'btn-dl-json'}" download>
                            <i class="bi bi-download"></i> 下载
                        </a>
                    </div>`;
                });
            } catch (e) {
                html = '<p style="color:var(--muted);font-size:12px;padding:8px 0">无法加载结果文件列表</p>';
            }
        }

        body.innerHTML = html || '<p style="color:var(--muted);font-size:12px;padding:8px 0">未检测到输出文件</p>';

        // Scroll into view
        section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

// ==================== Config Panel Init ====================
function initConfigPanel() {
    const configForm = document.getElementById('config-form');
    if (!configForm) return;

    // 自动保存配置（带防抖）
    let autoSaveTimeout;
    const autoSaveConfig = () => {
        clearTimeout(autoSaveTimeout);
        autoSaveTimeout = setTimeout(async () => {
            try {
                const data = await saveConfigNow();
                if (data.status === 'success') {
                    const saveIndicator = document.getElementById('save-indicator');
                    if (saveIndicator) {
                        saveIndicator.textContent = '✓ 已保存';
                        saveIndicator.style.opacity = '1';
                        setTimeout(() => {
                            saveIndicator.style.opacity = '0';
                        }, 2000);
                    }
                }
            } catch (error) {
                console.error('自动保存配置失败:', error);
            }
        }, 1000);
    };

    // 监听所有配置输入框的变化
    const configInputs = [
        'scraper-api-keys',
        'openai-api-key',
        'openai-base-url',
        'openai-model',
        'output-prefix',
        'sleep-between-pages',
        'parallel-author-search',
        'resume-page',
        'enable-year-traverse',
        'debug-mode',
        'test-mode',
        'retry-max-attempts',
        'retry-intervals',
        'scraper-premium',
        'scraper-ultra-premium',
        'scraper-session',
        'scholar-no-filter',
        'scraper-geo-rotate',
        'author-search-prompt1',
        'author-search-prompt2',
        'enable-renowned-scholar',
        'renowned-scholar-model',
        'renowned-scholar-prompt',
        'enable-author-verification',
        'author-verify-model',
        'author-verify-prompt',
        'enable-citing-description',
        'enable-dashboard',
        'dashboard-model'
    ];

    configInputs.forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.addEventListener('input', autoSaveConfig);
            element.addEventListener('change', autoSaveConfig);
        }
    });

    // 监听重要学者筛选开关，控制高级配置显示/隐藏
    const enableRenownedScholar = document.getElementById('enable-renowned-scholar');
    const renownedScholarConfig = document.getElementById('renowned-scholar-config');
    if (enableRenownedScholar && renownedScholarConfig) {
        enableRenownedScholar.addEventListener('change', () => {
            renownedScholarConfig.style.display = enableRenownedScholar.checked ? 'block' : 'none';
        });
    }

    // 监听作者信息校验开关，控制高级配置显示/隐藏
    const enableAuthorVerification = document.getElementById('enable-author-verification');
    const authorVerificationConfig = document.getElementById('author-verification-config');
    if (enableAuthorVerification && authorVerificationConfig) {
        enableAuthorVerification.addEventListener('change', () => {
            authorVerificationConfig.style.display = enableAuthorVerification.checked ? 'block' : 'none';
        });
    }

    // 保存配置（手动触发）
    configForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        clearTimeout(autoSaveTimeout);
        autoSaveConfig();
    });

    // 测试API
    document.getElementById('test-api-btn')?.addEventListener('click', async function() {
        const apiKey = document.getElementById('openai-api-key').value;
        const baseUrl = document.getElementById('openai-base-url').value;
        const model = document.getElementById('openai-model').value;
        const testQuery = document.getElementById('test-query').value;

        if (!apiKey || !baseUrl || !model) {
            alert('请先填写完整的API配置（API Key、Base URL、模型名称）');
            return;
        }

        if (!testQuery) {
            alert('请输入测试问题');
            return;
        }

        const btn = this;
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 测试中...';

        const resultDiv = document.getElementById('api-test-result');
        const alertDiv = document.getElementById('api-test-alert');

        try {
            const response = await fetch('/api/test_openai', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    api_key: apiKey,
                    base_url: baseUrl,
                    model: model,
                    test_query: testQuery
                })
            });

            const data = await response.json();

            resultDiv.style.display = 'block';

            if (data.status === 'success') {
                if (data.has_web_search) {
                    alertDiv.className = 'alert alert-success';
                    alertDiv.innerHTML = `
                        <strong><i class="bi bi-check-circle-fill"></i> ${data.message}</strong>
                        <hr>
                        <div class="mt-2">
                            <strong>Web Search功能:</strong> 已启用
                        </div>
                        <div class="mt-3">
                            <strong>测试问题:</strong>
                            <div class="bg-light p-2 mt-1 border rounded">${escapeHtml(testQuery)}</div>
                        </div>
                        <div class="mt-3">
                            <strong>不带Web Search的回答:</strong>
                            <div class="bg-light p-3 mt-1 border rounded" style="white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(data.test_results.without_web_search)}</div>
                        </div>
                        <div class="mt-3">
                            <strong>带Web Search的回答:</strong>
                            <div class="bg-light p-3 mt-1 border rounded" style="white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(data.test_results.with_web_search)}</div>
                        </div>
                    `;
                } else {
                    alertDiv.className = 'alert alert-warning';
                    alertDiv.innerHTML = `
                        <strong><i class="bi bi-exclamation-triangle-fill"></i> ${data.message}</strong>
                        <hr>
                        <div class="mt-2">
                            <strong>Web Search功能:</strong> 未检测到或不支持
                        </div>
                        <div class="mt-3">
                            <strong>测试问题:</strong>
                            <div class="bg-light p-2 mt-1 border rounded">${escapeHtml(testQuery)}</div>
                        </div>
                        <div class="mt-3">
                            <strong>不带Web Search的回答:</strong>
                            <div class="bg-light p-3 mt-1 border rounded" style="white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(data.test_results.without_web_search)}</div>
                        </div>
                        <div class="mt-3">
                            <strong>带Web Search的回答:</strong>
                            <div class="bg-light p-3 mt-1 border rounded" style="white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(data.test_results.with_web_search)}</div>
                        </div>
                    `;
                }
            } else {
                alertDiv.className = 'alert alert-danger';
                alertDiv.innerHTML = `
                    <strong><i class="bi bi-x-circle-fill"></i> 测试失败</strong>
                    <hr>
                    <div class="mt-2">${escapeHtml(data.message)}</div>
                `;
            }
        } catch (error) {
            resultDiv.style.display = 'block';
            alertDiv.className = 'alert alert-danger';
            alertDiv.innerHTML = `
                <strong><i class="bi bi-x-circle-fill"></i> 测试失败</strong>
                <hr>
                <div class="mt-2">网络错误: ${escapeHtml(error.toString())}</div>
            `;
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    });
}

// ==================== Results Panel Init ====================
function initResultsPanel() {
    // 刷新按钮
    document.getElementById('refresh-btn')?.addEventListener('click', () => {
        loadResults();
    });
}

// ==================== DOMContentLoaded ====================
document.addEventListener('DOMContentLoaded', function() {
    SpaRouter.init();
    GlobalProgress.init();
    initIndexPage();     // Home 面板
    initConfigPanel();   // Config 面板
    initResultsPanel();  // Results 面板
});
