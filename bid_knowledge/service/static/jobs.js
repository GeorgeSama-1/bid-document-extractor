(() => {
  const form = document.querySelector("#jobForm");
  const gpuSelect = form.elements.gpu_id;
  const keyInput = form.elements.api_key;
  const ppEnabled = form.elements.enable_pp_structure;
  const vlmEnabled = form.elements.enable_vlm_table;
  const submitButton = document.querySelector("#jobSubmit");
  const message = document.querySelector("#jobFormMessage");
  const jobsBody = document.querySelector("#jobsBody");
  const detail = document.querySelector("#jobDetail");
  const runSelect = document.querySelector("#runSelect");
  const toastRegion = document.querySelector("#toastRegion");
  let selectedJobId = "";
  let renderedJobId = "";
  let jobs = [];

  const booleanFields = [
    "enable_pp_structure",
    "pp_structure_use_doc_orientation_classify",
    "pp_structure_use_doc_unwarping",
    "pp_structure_use_textline_orientation",
    "enable_vlm_table",
  ];
  const statusLabels = {
    queued: "排队中", running: "运行中", succeeded: "已完成",
    failed: "失败", cancelled: "已取消",
  };

  function showToast(type, title, text, duration) {
    const key = `${type}:${title}:${text}`;
    const existing = [...toastRegion.children].find((node) => node.dataset.key === key);
    if (existing) {
      existing.classList.remove("leaving");
      return existing;
    }
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.dataset.key = key;
    toast.setAttribute("role", type === "error" ? "alert" : "status");
    const icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.textContent = type === "success" ? "✓" : type === "error" ? "!" : "i";
    const content = document.createElement("div");
    content.className = "toast-content";
    const heading = document.createElement("strong");
    heading.textContent = title;
    const body = document.createElement("span");
    body.textContent = text;
    content.append(heading, body);
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "关闭通知");
    close.textContent = "×";
    const remove = () => {
      if (!toast.isConnected || toast.classList.contains("leaving")) return;
      toast.classList.add("leaving");
      window.setTimeout(() => toast.remove(), 190);
    };
    close.addEventListener("click", remove);
    toast.append(icon, content, close);
    toastRegion.appendChild(toast);
    window.setTimeout(remove, duration ?? (type === "error" ? 7000 : 4000));
    return toast;
  }
  window.showServiceToast = showToast;

  async function request(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let text = await response.text();
      try { text = JSON.parse(text).detail || text; } catch (_) { /* use text */ }
      throw new Error(text || response.statusText);
    }
    return response.json();
  }

  async function loadGpus() {
    const data = await request("/api/system/gpus");
    gpuSelect.replaceChildren();
    for (const gpu of data.gpus) {
      const option = document.createElement("option");
      option.value = gpu.id;
      option.textContent = `GPU ${gpu.id} · ${gpu.name} · ${gpu.used_mib}/${gpu.total_mib} MiB`;
      gpuSelect.appendChild(option);
    }
    if (!data.gpus.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "没有可用 GPU";
      gpuSelect.appendChild(option);
    }
  }

  function progressText(job) {
    if (job.status === "queued") return `队列 ${job.queue_position || "-"}`;
    if (!job.progress?.total) return job.progress?.stage || "-";
    return `${job.progress.step}/${job.progress.total} ${job.progress.stage || ""}`;
  }

  function button(label, action, className = "") {
    const element = document.createElement("button");
    element.type = "button";
    element.textContent = label;
    element.className = className;
    element.addEventListener("click", action);
    return element;
  }

  function renderJobs() {
    jobsBody.replaceChildren();
    if (!jobs.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.className = "jobs-empty-row";
      cell.textContent = "暂无解析任务";
      row.appendChild(cell);
      jobsBody.appendChild(row);
      return;
    }
    for (const job of jobs) {
      const row = document.createElement("tr");
      if (job.id === selectedJobId) row.className = "selected";
      const name = document.createElement("td");
      const filename = document.createElement("strong");
      filename.textContent = job.original_filename;
      const id = document.createElement("small");
      id.textContent = job.id.slice(0, 8);
      name.append(filename, id);
      const status = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `status-badge ${job.status}`;
      badge.textContent = statusLabels[job.status] || job.status;
      status.appendChild(badge);
      const gpu = document.createElement("td");
      gpu.textContent = `GPU ${job.gpu_id}`;
      const progress = document.createElement("td");
      progress.textContent = progressText(job);
      const actions = document.createElement("td");
      actions.className = "job-row-actions";
      actions.appendChild(button("详情", () => selectJob(job.id)));
      if (["queued", "running"].includes(job.status)) {
        actions.appendChild(button("取消", () => cancelJob(job.id), "warning-button"));
      } else {
        actions.appendChild(button("删除", () => deleteJob(job.id), "danger-link"));
      }
      row.append(name, status, gpu, progress, actions);
      jobsBody.appendChild(row);
    }
  }

  async function loadJobs() {
    jobs = (await request("/api/jobs")).jobs;
    renderJobs();
    if (selectedJobId) {
      if (jobs.some((job) => job.id === selectedJobId)) await loadDetail(selectedJobId);
      else clearDetail();
    }
  }

  async function selectJob(jobId) {
    selectedJobId = jobId;
    renderJobs();
    await loadDetail(jobId);
  }

  function detailCard(titleText, className = "") {
    const card = document.createElement("section");
    card.className = `detail-card ${className}`.trim();
    const heading = document.createElement("h3");
    heading.textContent = titleText;
    card.appendChild(heading);
    return card;
  }

  function ensureDetail(job) {
    if (renderedJobId === job.id) return;
    renderedJobId = job.id;
    detail.className = "job-detail";
    detail.replaceChildren();

    const header = document.createElement("header");
    header.className = "detail-header";
    const heading = document.createElement("h2");
    heading.dataset.role = "filename";
    const status = document.createElement("span");
    status.dataset.role = "status";
    header.append(heading, status);

    const summary = detailCard("运行信息");
    const grid = document.createElement("dl");
    grid.className = "detail-grid";
    for (const [label, role] of [["进度", "progress"], ["计算设备", "gpu"], ["版面分析", "pp"], ["表格增强", "vlm"]]) {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.dataset.role = role;
      grid.append(dt, dd);
    }
    summary.appendChild(grid);

    const error = detailCard("错误信息", "error-card");
    error.dataset.role = "error-card";
    const errorText = document.createElement("p");
    errorText.dataset.role = "error";
    error.appendChild(errorText);

    const logs = detailCard("实时日志", "log-card");
    const log = document.createElement("pre");
    log.className = "job-log";
    log.dataset.role = "log";
    logs.appendChild(log);

    const actions = detailCard("结果操作", "result-actions");
    actions.dataset.role = "actions";
    detail.append(header, summary, error, logs, actions);
  }

  async function loadDetail(jobId) {
    const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (jobId !== selectedJobId) return;
    ensureDetail(job);
    detail.querySelector('[data-role="filename"]').textContent = job.original_filename;
    const status = detail.querySelector('[data-role="status"]');
    status.className = `status-badge ${job.status}`;
    status.textContent = statusLabels[job.status] || job.status;
    detail.querySelector('[data-role="progress"]').textContent = progressText(job);
    detail.querySelector('[data-role="gpu"]').textContent = `GPU ${job.gpu_id}`;
    detail.querySelector('[data-role="pp"]').textContent = job.parameters.enable_pp_structure
      ? `启用 · ${String(job.parameters.pp_structure_device).toUpperCase()}` : "关闭";
    detail.querySelector('[data-role="vlm"]').textContent = job.parameters.enable_vlm_table
      ? `启用 · ${job.parameters.vlm_model}` : "关闭";

    const errorCard = detail.querySelector('[data-role="error-card"]');
    errorCard.hidden = !job.error;
    detail.querySelector('[data-role="error"]').textContent = job.error || "";

    const log = detail.querySelector('[data-role="log"]');
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 28;
    const previousTop = log.scrollTop;
    const nextLog = (job.logs || []).join("\n") || "暂无日志";
    if (log.textContent !== nextLog) {
      log.textContent = nextLog;
      log.scrollTop = atBottom ? log.scrollHeight : previousTop;
    }
    renderResultActions(job);
  }

  function renderResultActions(job) {
    const actions = detail.querySelector('[data-role="actions"]');
    actions.querySelectorAll("a, button, p").forEach((node) => node.remove());
    if (job.status !== "succeeded") {
      const hint = document.createElement("p");
      hint.className = "muted";
      hint.textContent = "任务成功后可下载素材压缩包或浏览目录结果。";
      actions.appendChild(hint);
      return;
    }
    const archive = document.createElement("a");
    archive.className = "primary-link";
    archive.href = `/api/jobs/${encodeURIComponent(job.id)}/archive`;
    archive.download = `job_${job.id}.zip`;
    archive.textContent = "下载完整 ZIP";
    const browse = button("浏览目录结果", async () => {
      await window.resultBrowserLoadRuns();
      runSelect.value = job.run_name;
      runSelect.dispatchEvent(new Event("change"));
      document.querySelector(".app").scrollIntoView({ behavior: "smooth" });
    });
    actions.append(archive, browse);
  }

  function clearDetail() {
    selectedJobId = "";
    renderedJobId = "";
    detail.className = "job-detail empty";
    detail.textContent = "选择任务查看详情、日志和结果文件。";
  }

  async function cancelJob(jobId) {
    try {
      await request(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
      selectedJobId = jobId;
      await loadJobs();
      showToast("success", "任务已取消", `任务 ${jobId.slice(0, 8)} 已停止运行。`);
    } catch (error) {
      showError(error);
    }
  }

  async function deleteJob(jobId) {
    if (!window.confirm("确定删除该任务的记录、上传文件、日志和解析结果吗？")) return;
    try {
      await request(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
      if (selectedJobId === jobId) clearDetail();
      await Promise.all([loadJobs(), window.resultBrowserLoadRuns()]);
      showToast("success", "任务已删除", `任务 ${jobId.slice(0, 8)} 及其解析数据已清理。`);
    } catch (error) {
      showError(error);
    }
  }

  async function clearHistory() {
    if (!window.confirm("确定清空所有已结束任务及其解析数据吗？正在排队或运行的任务会保留。")) return;
    const result = await request("/api/jobs", { method: "DELETE" });
    clearDetail();
    const summary = `已删除 ${result.deleted.length} 个任务${result.active.length ? `，保留 ${result.active.length} 个运行中任务` : ""}。`;
    message.textContent = summary;
    await Promise.all([loadJobs(), window.resultBrowserLoadRuns()]);
    showToast("success", "历史记录已清理", summary);
  }

  function syncEngineFields() {
    form.elements.pp_structure_device.disabled = !ppEnabled.checked;
    for (const name of ["pp_structure_use_doc_orientation_classify", "pp_structure_use_doc_unwarping", "pp_structure_use_textline_orientation"]) {
      form.elements[name].disabled = !ppEnabled.checked;
    }
    for (const input of form.querySelectorAll(".vlm-fields input")) input.disabled = !vlmEnabled.checked;
    form.elements.vlm_endpoint.required = vlmEnabled.checked;
    form.elements.vlm_model.required = vlmEnabled.checked;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submitButton.disabled = true;
    message.textContent = "上传中…";
    showToast("info", "正在创建任务", "正在上传 PDF 并保存任务配置，请稍候。", 3000);
    const data = new FormData(form);
    for (const name of booleanFields) data.set(name, String(form.elements[name].checked));
    try {
      const job = await request("/api/jobs", { method: "POST", body: data });
      keyInput.value = "";
      selectedJobId = job.id;
      message.textContent = `任务 ${job.id.slice(0, 8)} 已创建`;
      await loadJobs();
      showToast("success", "任务创建成功", `任务 ${job.id.slice(0, 8)} 已进入解析队列。`);
    } catch (error) {
      showError(error);
    } finally {
      submitButton.disabled = false;
    }
  });

  function showError(error) {
    message.textContent = error.message;
    showToast("error", "操作失败", error.message);
  }

  ppEnabled.addEventListener("change", syncEngineFields);
  vlmEnabled.addEventListener("change", syncEngineFields);
  document.querySelector("#jobsRefresh").addEventListener("click", async () => {
    try {
      await loadJobs();
      showToast("success", "任务列表已刷新", "已获取最新任务状态。", 2800);
    } catch (error) {
      showError(error);
    }
  });
  document.querySelector("#jobsClearHistory").addEventListener("click", () => clearHistory().catch(showError));
  syncEngineFields();
  Promise.all([loadGpus(), loadJobs()]).catch(showError);
  window.setInterval(() => {
    if (jobs.some((job) => ["queued", "running"].includes(job.status))) loadJobs().catch(showError);
  }, 2000);
})();
