// ==================== 新首页逻辑（自动化流水线）====================
function initIndexPage() {
    const runBtn = document.getElementById('idx-run-btn');
    if (!runBtn) return;

    // 初始化 WebSocket
    const ws = new WebSocketManager();
    ws.connect();

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

    // 加载配置并填充表单
    (async () => {
        try {
            const resp = await fetch('/api/config');
            const cfg = await resp.json();
            const el = id => document.getElementById(id);
            el('idx-scraper-keys').value = (cfg.scraper_api_keys || []).join(',');
            el('idx-openai-key').value = cfg.openai_api_key || '';
            el('idx-openai-url').value = cfg.openai_base_url || '';
            el('idx-openai-model').value = cfg.openai_model || '';
            el('idx-output-prefix').value = cfg.default_output_prefix || 'paper';
            el('idx-renowned-scholar').checked = cfg.enable_renowned_scholar_filter !== false;
            el('idx-author-verify').checked = cfg.enable_author_verification || false;
            el('idx-citing-description').checked = cfg.enable_citing_description !== false;
            el('idx-dashboard').checked = cfg.enable_dashboard !== false;
            el('idx-dashboard-model').value = cfg.dashboard_model || 'gemini-3-flash-preview-nothinking';
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
            enable_citing_description: el('idx-citing-description').checked,
            enable_dashboard: el('idx-dashboard').checked,
            dashboard_model: el('idx-dashboard-model').value,
        };
        try {
            const cfgResp = await fetch('/api/config');
            const existing = await cfgResp.json();
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

    // WebSocket 事件监听
    ws.on('log', log => appendIndexLog(log));
    ws.on('history', logs => logs.forEach(log => appendIndexLog(log)));
    ws.on('progress', progress => updateIndexProgress(progress));
    ws.on('all_done', data => showIndexResults(data));

    // 开始分析按钮
    runBtn.addEventListener('click', async () => {
        const titlesRaw = document.getElementById('paper-titles').value;
        const titles = titlesRaw.split('\n').map(t => t.trim()).filter(t => t);
        if (titles.length === 0) {
            alert('请输入至少一篇论文题目');
            return;
        }
        await saveIndexConfig();
        const outputPrefix = document.getElementById('idx-output-prefix').value || 'paper';

        runBtn.disabled = true;
        runBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" style="animation:spin .8s linear infinite"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2.5" stroke-dasharray="40" stroke-dashoffset="10"/></svg>&nbsp; 运行中...';

        document.getElementById('idx-cancel-btn').style.display = 'inline-flex';
        document.getElementById('idx-progress-section').style.display = 'block';
        document.getElementById('idx-log-section').style.display = 'block';
        document.getElementById('idx-results-section').style.display = 'none';

        // 清空日志，显示 empty placeholder
        document.getElementById('idx-log-container').innerHTML =
            '<div class="reasoning-empty"><div class="reasoning-empty-icon">🤖</div><div class="reasoning-empty-text">智能体正在初始化...</div></div>';

        // 显示 thinking indicator
        const thinking = document.getElementById('rp-thinking-indicator');
        if (thinking) thinking.classList.add('active');

        // 重置进度
        updateIndexProgress({ percentage: 0, current: 0, total: 0 });
        currentPhase = '初始化中...';
        document.getElementById('idx-phase-label').textContent = currentPhase;

        try {
            const resp = await fetch('/api/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paper_titles: titles, output_prefix: outputPrefix })
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
    });

    // 清空日志
    document.getElementById('idx-clear-log-btn').addEventListener('click', () => {
        document.getElementById('idx-log-container').innerHTML =
            '<div class="reasoning-empty"><div class="reasoning-empty-icon">🧹</div><div class="reasoning-empty-text">日志已清空</div></div>';
    });

    function resetRunBtn() {
        runBtn.disabled = false;
        runBtn.innerHTML = '<i class="bi bi-play-fill"></i> 开始分析';
        document.getElementById('idx-cancel-btn').style.display = 'none';
        const thinking = document.getElementById('rp-thinking-indicator');
        if (thinking) thinking.classList.remove('active');
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

    async function showIndexResults(data) {
        resetRunBtn();
        const section = document.getElementById('idx-results-section');
        const body    = document.getElementById('idx-results-body');
        section.style.display = 'block';

        let html = '';

        if (data && data.excel) {
            const name = data.excel.split('/').pop();
            html += `<div class="result-file-row">
                <span class="result-file-icon">📊</span>
                <span class="result-file-name">${name}</span>
                <a href="/api/results/download/${encodeURIComponent(name)}" class="btn-download btn-dl-excel" download>
                    <i class="bi bi-download"></i> Excel
                </a>
            </div>`;
        }
        if (data && data.json) {
            const name = data.json.split('/').pop();
            html += `<div class="result-file-row">
                <span class="result-file-icon">📋</span>
                <span class="result-file-name">${name}</span>
                <a href="/api/results/download/${encodeURIComponent(name)}" class="btn-download btn-dl-json" download>
                    <i class="bi bi-download"></i> JSON
                </a>
            </div>`;
        }
        if (data && data.dashboard) {
            const name = data.dashboard.split('/').pop();
            html += `<div class="dashboard-cta">
                <span class="result-file-icon">🔭</span>
                <div class="dashboard-cta-text">
                    <strong style="color:#bc8cff">多维画像分析报告已生成</strong><br>
                    <span style="font-size:11.5px">${name}</span>
                </div>
                <a href="/api/results/view/${encodeURIComponent(name)}" target="_blank" class="btn-download btn-dl-report">
                    <i class="bi bi-eye"></i> 查看报告
                </a>
            </div>`;
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

// 主JavaScript逻辑
document.addEventListener('DOMContentLoaded', function() {
    // 初始化新首页
    initIndexPage();

    // ==================== 首页逻辑 ====================
    const startBrowserBtn = document.getElementById('start-browser-btn');
    if (startBrowserBtn) {
        // 初始化WebSocket
        wsManager = new WebSocketManager();
        wsManager.connect();

        // 监听URL捕获事件
        wsManager.on('url_captured', (data) => {
            console.log('捕获到URL:', data.url);
            // 跳转到配置页面
            window.location.href = '/config';
        });

        // 启动浏览器按钮
        startBrowserBtn.addEventListener('click', async () => {
            startBrowserBtn.disabled = true;
            startBrowserBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 启动中...';

            try {
                const response = await fetch('/api/browser/start', { method: 'POST' });
                const data = await response.json();

                if (data.status === 'success') {
                    document.getElementById('status-display').innerHTML =
                        '<span class="badge bg-success fs-6">浏览器已启动</span>';
                    document.getElementById('waiting-message').style.display = 'block';
                } else {
                    alert('启动失败: ' + data.message);
                    startBrowserBtn.disabled = false;
                    startBrowserBtn.innerHTML = '<i class="bi bi-play-circle"></i> 启动浏览器';
                }
            } catch (error) {
                console.error('启动浏览器失败:', error);
                alert('启动失败,请检查控制台');
                startBrowserBtn.disabled = false;
                startBrowserBtn.innerHTML = '<i class="bi bi-play-circle"></i> 启动浏览器';
            }
        });
    }

    // ==================== 配置页逻辑 ====================
    const configForm = document.getElementById('config-form');
    if (configForm) {
        // 加载配置
        loadConfig();

        // 检查已有文件
        checkExistingFiles();

        // 从表单读取当前配置
        function collectConfig() {
            return {
                scraper_api_keys: document.getElementById('scraper-api-keys').value
                    .split(',').map(k => k.trim()).filter(k => k),
                openai_api_key: document.getElementById('openai-api-key').value,
                openai_base_url: document.getElementById('openai-base-url').value,
                openai_model: document.getElementById('openai-model').value,
                default_output_prefix: document.getElementById('output-prefix').value,
                sleep_between_pages: parseInt(document.getElementById('sleep-between-pages').value) || 10,
                parallel_author_search: parseInt(document.getElementById('parallel-author-search').value) || 1,
                resume_page_count: parseInt(document.getElementById('resume-page').value) || 0,
                enable_year_traverse: document.getElementById('enable-year-traverse').checked,
                debug_mode: document.getElementById('debug-mode').checked,
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
                author_verify_prompt: document.getElementById('author-verify-prompt').value
            };
        }

        // 立即保存配置到服务器
        async function saveConfigNow() {
            const config = collectConfig();
            const response = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });
            return await response.json();
        }

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
            }, 1000); // 1秒防抖
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
            'author-verify-prompt'
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
            // 清除防抖计时器，立即保存
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
                                <strong>✅ Web Search功能:</strong> 已启用
                            </div>
                            <div class="mt-3">
                                <strong>📝 测试问题:</strong>
                                <div class="bg-light p-2 mt-1 border rounded">${escapeHtml(testQuery)}</div>
                            </div>
                            <div class="mt-3">
                                <strong>🔍 不带Web Search的回答:</strong>
                                <div class="bg-light p-3 mt-1 border rounded" style="white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(data.test_results.without_web_search)}</div>
                            </div>
                            <div class="mt-3">
                                <strong>🌐 带Web Search的回答:</strong>
                                <div class="bg-light p-3 mt-1 border rounded" style="white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(data.test_results.with_web_search)}</div>
                            </div>
                        `;
                    } else {
                        alertDiv.className = 'alert alert-warning';
                        alertDiv.innerHTML = `
                            <strong><i class="bi bi-exclamation-triangle-fill"></i> ${data.message}</strong>
                            <hr>
                            <div class="mt-2">
                                <strong>⚠️ Web Search功能:</strong> 未检测到或不支持
                            </div>
                            <div class="mt-3">
                                <strong>📝 测试问题:</strong>
                                <div class="bg-light p-2 mt-1 border rounded">${escapeHtml(testQuery)}</div>
                            </div>
                            <div class="mt-3">
                                <strong>🔍 不带Web Search的回答:</strong>
                                <div class="bg-light p-3 mt-1 border rounded" style="white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(data.test_results.without_web_search)}</div>
                            </div>
                            <div class="mt-3">
                                <strong>🌐 带Web Search的回答:</strong>
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

        // 开始任务（仅跳转到任务页面，不自动开始）
        document.getElementById('start-task-btn').addEventListener('click', async () => {
            const url = document.getElementById('captured-url')?.textContent?.trim();

            if (!url) {
                alert('未检测到URL,请先从首页启动浏览器捕获URL');
                return;
            }

            // 跳转前强制保存配置（取消 debounce，立即执行）
            clearTimeout(autoSaveTimeout);
            try {
                await saveConfigNow();
            } catch (e) {
                console.error('跳转前保存配置失败:', e);
            }

            // 保存URL和配置到sessionStorage
            const outputPrefix = document.getElementById('output-prefix').value;
            const resumePage = parseInt(document.getElementById('resume-page').value) || 0;
            sessionStorage.setItem('task_url', url);
            sessionStorage.setItem('task_output_prefix', outputPrefix);
            sessionStorage.setItem('task_resume_page', resumePage);

            // 跳转到任务页面
            window.location.href = '/task';
        });
    }

    // ==================== 任务页逻辑 ====================
    const logContainer = document.getElementById('log-container');
    if (logContainer) {
        // 初始化WebSocket
        wsManager = new WebSocketManager();
        wsManager.connect();

        let autoScroll = true;

        // 监听日志
        wsManager.on('log', (log) => {
            appendLog(log);
        });

        // 监听历史日志
        wsManager.on('history', (logs) => {
            logs.forEach(log => appendLog(log));
        });

        // 监听进度
        wsManager.on('progress', (progress) => {
            updateProgress(progress);
        });

        // 监听阶段1完成事件
        wsManager.on('stage1_complete', (data) => {
            console.log('阶段1完成:', data);
            // 隐藏初始选择
            document.getElementById('initial-choice').style.display = 'none';
            // 隐藏取消按钮
            document.getElementById('cancel-btn').style.display = 'none';
            // 显示继续按钮
            document.getElementById('continue-btn').style.display = 'block';
        });

        // 开始抓取按钮
        document.getElementById('start-scraping-btn')?.addEventListener('click', async () => {
            const url = sessionStorage.getItem('task_url');
            const outputPrefix = sessionStorage.getItem('task_output_prefix') || 'paper';
            const resumePage = parseInt(sessionStorage.getItem('task_resume_page')) || 0;

            if (!url) {
                alert('未检测到URL，请从配置页面重新进入');
                window.location.href = '/config';
                return;
            }

            if (!confirm('确定要开始抓取Google Scholar引用列表吗？')) {
                return;
            }

            const startBtn = document.getElementById('start-scraping-btn');
            startBtn.disabled = true;
            startBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 启动中...';

            try {
                const response = await fetch('/api/task/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, output_prefix: outputPrefix, resume_page: resumePage })
                });

                const data = await response.json();

                if (data.status === 'success') {
                    // 隐藏初始选择
                    document.getElementById('initial-choice').style.display = 'none';
                    // 显示取消按钮
                    document.getElementById('cancel-btn').style.display = 'block';
                } else {
                    alert('启动失败: ' + data.message);
                    startBtn.disabled = false;
                    startBtn.innerHTML = '<i class="bi bi-play-circle"></i> 开始抓取';
                }
            } catch (error) {
                console.error('启动抓取失败:', error);
                alert('启动失败，请检查控制台');
                startBtn.disabled = false;
                startBtn.innerHTML = '<i class="bi bi-play-circle"></i> 开始抓取';
            }
        });

        // 导入历史记录按钮
        document.getElementById('import-history-btn')?.addEventListener('click', async () => {
            // 创建文件选择器
            const fileInput = document.createElement('input');
            fileInput.type = 'file';
            fileInput.accept = '.jsonl';
            fileInput.onchange = async (e) => {
                const file = e.target.files[0];
                if (!file) return;

                if (!file.name.endsWith('.jsonl')) {
                    alert('请选择.jsonl文件');
                    return;
                }

                const importBtn = document.getElementById('import-history-btn');
                importBtn.disabled = true;
                importBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 导入中...';

                try {
                    const formData = new FormData();
                    formData.append('file', file);

                    const response = await fetch('/api/task/import', {
                        method: 'POST',
                        body: formData
                    });

                    const data = await response.json();

                    if (data.status === 'success') {
                        alert(`导入成功！\n文件: ${data.file_name}\n论文数: ${data.paper_count}`);
                        // 隐藏初始选择
                        document.getElementById('initial-choice').style.display = 'none';
                        // 显示继续按钮
                        document.getElementById('continue-btn').style.display = 'block';
                    } else {
                        alert('导入失败: ' + data.message);
                        importBtn.disabled = false;
                        importBtn.innerHTML = '<i class="bi bi-folder-open"></i> 导入历史记录';
                    }
                } catch (error) {
                    console.error('导入失败:', error);
                    alert('导入失败，请检查控制台');
                    importBtn.disabled = false;
                    importBtn.innerHTML = '<i class="bi bi-folder-open"></i> 导入历史记录';
                }
            };
            fileInput.click();
        });

        // 继续按钮点击事件
        document.getElementById('continue-btn')?.addEventListener('click', async () => {
            if (!confirm('确定要继续执行阶段2和3吗？\n这将开始搜索作者信息，可能会消耗较多API配额。')) {
                return;
            }

            const continueBtn = document.getElementById('continue-btn');
            continueBtn.disabled = true;
            continueBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 启动中...';

            try {
                const response = await fetch('/api/task/continue', { method: 'POST' });
                const data = await response.json();

                if (data.status === 'success') {
                    // 隐藏继续按钮
                    continueBtn.style.display = 'none';
                    // 显示取消按钮
                    const cancelBtn = document.getElementById('cancel-btn');
                    if (cancelBtn) {
                        cancelBtn.style.display = 'block';
                    }
                } else {
                    alert('启动失败: ' + data.message);
                    continueBtn.disabled = false;
                    continueBtn.innerHTML = '<i class="bi bi-play-fill"></i> 继续执行阶段2/3';
                }
            } catch (error) {
                console.error('启动阶段2/3失败:', error);
                alert('启动失败，请检查控制台');
                continueBtn.disabled = false;
                continueBtn.innerHTML = '<i class="bi bi-play-fill"></i> 继续执行阶段2/3';
            }
        });

        // 添加日志到终端
        function appendLog(log) {
            // 移除"等待日志"提示
            const waitingMsg = logContainer.querySelector('.text-muted');
            if (waitingMsg) {
                logContainer.innerHTML = '';
            }

            const logEntry = document.createElement('div');
            logEntry.className = 'log-entry';

            let levelColor = '#d4d4d4';
            let levelIcon = 'info-circle';
            if (log.level === 'ERROR') {
                levelColor = '#f48771';
                levelIcon = 'x-circle';
            } else if (log.level === 'WARNING') {
                levelColor = '#dcdcaa';
                levelIcon = 'exclamation-triangle';
            } else if (log.level === 'SUCCESS') {
                levelColor = '#4ec9b0';
                levelIcon = 'check-circle';
            }

            logEntry.innerHTML = `
                <span class="log-timestamp">[${log.timestamp}]</span>
                <span class="log-level" style="color: ${levelColor};">
                    <i class="bi bi-${levelIcon}"></i> [${log.level}]
                </span>
                <span class="log-message">${escapeHtml(log.message)}</span>
            `;

            logContainer.appendChild(logEntry);

            // 更新任务阶段
            updateStage(log.message);

            // 自动滚动
            if (autoScroll) {
                logContainer.scrollTop = logContainer.scrollHeight;
            }
        }

        // 更新进度条
        function updateProgress(progress) {
            const progressBar = document.getElementById('progress-bar');
            const progressText = document.getElementById('progress-text');

            progressBar.style.width = progress.percentage + '%';
            progressBar.textContent = progress.percentage + '%';
            progressBar.setAttribute('aria-valuenow', progress.percentage);

            if (progressText) {
                progressText.innerHTML = `<i class="bi bi-hourglass-split"></i> ${progress.current} / ${progress.total}`;
            }
        }

        // 更新任务阶段显示
        function updateStage(message) {
            if (message.includes('阶段1')) {
                updateStageIcon('stage-1', 'play-circle', 'primary');
            } else if (message.includes('阶段2')) {
                updateStageIcon('stage-1', 'check-circle', 'success');
                updateStageIcon('stage-2', 'play-circle', 'primary');
            } else if (message.includes('阶段3')) {
                updateStageIcon('stage-2', 'check-circle', 'success');
                updateStageIcon('stage-3', 'play-circle', 'primary');
            } else if (message.includes('全部任务完成')) {
                updateStageIcon('stage-3', 'check-circle', 'success');
            }
        }

        function updateStageIcon(stageId, icon, color) {
            const stage = document.getElementById(stageId);
            if (stage) {
                const iconEl = stage.querySelector('i');
                iconEl.className = `bi bi-${icon} text-${color}`;
            }
        }

        // 清空日志
        document.getElementById('clear-logs-btn')?.addEventListener('click', () => {
            logContainer.innerHTML = '<div class="text-muted text-center p-4">日志已清空</div>';
        });

        // 自动滚动切换
        document.getElementById('auto-scroll-btn')?.addEventListener('click', function() {
            autoScroll = !autoScroll;
            this.classList.toggle('active');
        });

        // 取消任务
        document.getElementById('cancel-btn')?.addEventListener('click', async () => {
            if (!confirm('确定要取消当前任务吗?')) {
                return;
            }

            try {
                const response = await fetch('/api/task/cancel', { method: 'POST' });
                const data = await response.json();
                if (data.status === 'success') {
                    showToast('任务取消中...', 'warning');
                }
            } catch (error) {
                console.error('取消任务失败:', error);
            }
        });
    }

    // ==================== 结果页逻辑 ====================
    const resultsTable = document.getElementById('results-table');
    if (resultsTable) {
        loadResults();

        // 刷新按钮
        document.getElementById('refresh-btn')?.addEventListener('click', () => {
            loadResults();
        });
    }

    // ==================== 工具函数 ====================
    async function checkExistingFiles() {
        try {
            const response = await fetch('/api/results/list');
            const files = await response.json();

            // 过滤出JSONL文件
            const jsonlFiles = files.filter(f => f.type === '.jsonl');

            if (jsonlFiles.length > 0) {
                const alertDiv = document.getElementById('existing-files-alert');
                const listDiv = document.getElementById('existing-files-list');

                // 按修改时间排序（最新的在前）
                jsonlFiles.sort((a, b) => b.modified - a.modified);

                // 显示最近的5个文件
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

    async function loadConfig() {
        try {
            const response = await fetch('/api/config');
            const config = await response.json();

            document.getElementById('scraper-api-keys').value = config.scraper_api_keys.join(',');
            document.getElementById('openai-api-key').value = config.openai_api_key;
            document.getElementById('openai-base-url').value = config.openai_base_url;
            document.getElementById('openai-model').value = config.openai_model;
            document.getElementById('output-prefix').value = config.default_output_prefix;
            document.getElementById('sleep-between-pages').value = config.sleep_between_pages || 10;
            document.getElementById('parallel-author-search').value = config.parallel_author_search || 1;
            document.getElementById('resume-page').value = config.resume_page_count;
            document.getElementById('enable-year-traverse').checked = config.enable_year_traverse || false;
            document.getElementById('debug-mode').checked = config.debug_mode || false;
            document.getElementById('retry-max-attempts').value = config.retry_max_attempts || 3;
            document.getElementById('retry-intervals').value = config.retry_intervals || '5,10,20';
            document.getElementById('scraper-premium').checked = config.scraper_premium || false;
            document.getElementById('scraper-ultra-premium').checked = config.scraper_ultra_premium || false;
            document.getElementById('scraper-session').checked = config.scraper_session || false;
            document.getElementById('scholar-no-filter').checked = config.scholar_no_filter || false;
            document.getElementById('scraper-geo-rotate').checked = config.scraper_geo_rotate || false;
            document.getElementById('author-search-prompt1').value = config.author_search_prompt1 || '';
            document.getElementById('author-search-prompt2').value = config.author_search_prompt2 || '';
            document.getElementById('enable-renowned-scholar').checked = config.enable_renowned_scholar_filter || false;
            document.getElementById('renowned-scholar-model').value = config.renowned_scholar_model || 'gpt-5-nano';
            document.getElementById('renowned-scholar-prompt').value = config.renowned_scholar_prompt || '';

            // 根据enable_renowned_scholar_filter设置显示/隐藏高级配置
            const renownedScholarConfig = document.getElementById('renowned-scholar-config');
            if (renownedScholarConfig) {
                renownedScholarConfig.style.display = config.enable_renowned_scholar_filter ? 'block' : 'none';
            }

            // 加载作者校验配置
            document.getElementById('enable-author-verification').checked = config.enable_author_verification || false;
            document.getElementById('author-verify-model').value = config.author_verify_model || 'gemini-3-pro-preview-search';
            document.getElementById('author-verify-prompt').value = config.author_verify_prompt || '';

            // 根据enable_author_verification设置显示/隐藏高级配置
            const authorVerificationConfig = document.getElementById('author-verification-config');
            if (authorVerificationConfig) {
                authorVerificationConfig.style.display = config.enable_author_verification ? 'block' : 'none';
            }
        } catch (error) {
            console.error('加载配置失败:', error);
        }
    }

    async function loadResults() {
        const loading = document.getElementById('loading-indicator');
        const empty = document.getElementById('empty-state');
        const tableContainer = document.getElementById('results-table-container');
        const table = document.getElementById('results-table');

        loading.style.display = 'block';
        empty.style.display = 'none';
        tableContainer.style.display = 'none';

        try {
            const response = await fetch('/api/results/list');
            const files = await response.json();

            loading.style.display = 'none';

            if (files.length === 0) {
                empty.style.display = 'block';
            } else {
                tableContainer.style.display = 'block';
                table.innerHTML = '';

                files.forEach(file => {
                    const row = document.createElement('tr');

                    const typeClass = file.type === '.xlsx' ? 'success' :
                                    file.type === '.json' ? 'info' : 'warning';

                    const size = (file.size / 1024).toFixed(2);
                    const date = new Date(file.modified * 1000).toLocaleString('zh-CN');

                    row.innerHTML = `
                        <td>
                            <i class="bi bi-file-earmark-${file.type === '.xlsx' ? 'excel' : 'code'}"></i>
                            ${file.name}
                        </td>
                        <td><span class="badge bg-${typeClass}">${file.type}</span></td>
                        <td>${size} KB</td>
                        <td>${date}</td>
                        <td>
                            <a href="/api/results/download/${file.name}"
                               class="btn btn-sm btn-primary"
                               download>
                                <i class="bi bi-download"></i> 下载
                            </a>
                        </td>
                    `;
                    table.appendChild(row);
                });

                document.getElementById('file-count').textContent = files.length;
            }
        } catch (error) {
            console.error('加载结果失败:', error);
            loading.style.display = 'none';
            empty.style.display = 'block';
        }
    }

    function showToast(message, type = 'info') {
        // 简单的Toast通知
        alert(message);
    }

    function escapeHtml(unsafe) {
        return unsafe
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
});
