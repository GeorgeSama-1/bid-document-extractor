(() => {
  const form = document.querySelector("#jobForm");
  const gpuSelect = form.elements.gpu_id;
  const keyInput = form.elements.api_key;
  const submitButton = document.querySelector("#jobSubmit");
  const message = document.querySelector("#jobFormMessage");
  const jobsBody = document.querySelector("#jobsBody");
  const detail = document.querySelector("#jobDetail");
  let selectedJobId = "";
  let jobs = [];

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

  function addButton(cell, label, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", action);
    cell.appendChild(button);
  }

  function renderJobs() {
    jobsBody.replaceChildren();
    for (const job of jobs) {
      const row = document.createElement("tr");
      const name = document.createElement("td");
      name.textContent = `${job.original_filename} · ${job.id.slice(0, 8)}`;
      const status = document.createElement("td");
      status.className = `job-status ${job.status}`;
      status.textContent = job.status;
      const gpu = document.createElement("td");
      gpu.textContent = job.gpu_id;
      const progress = document.createElement("td");
      progress.textContent = progressText(job);
      const actions = document.createElement("td");
      addButton(actions, "详情", () => selectJob(job.id));
      if (["queued", "running"].includes(job.status)) addButton(actions, "取消", () => cancelJob(job.id));
      row.append(name, status, gpu, progress, actions);
      jobsBody.appendChild(row);
    }
  }

  async function loadJobs() {
    jobs = (await request("/api/jobs")).jobs;
    renderJobs();
    if (selectedJobId) await loadDetail(selectedJobId);
  }

  async function selectJob(jobId) {
    selectedJobId = jobId;
    await loadDetail(jobId);
  }

  async function loadDetail(jobId) {
    const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    detail.className = "job-detail";
    detail.replaceChildren();
    const heading = document.createElement("h2");
    heading.textContent = job.original_filename;
    const summary = document.createElement("div");
    summary.textContent = `${job.status} · GPU ${job.gpu_id} · ${progressText(job)}`;
    detail.append(heading, summary);
    if (job.error) {
      const error = document.createElement("p");
      error.className = "job-error";
      error.textContent = job.error;
      detail.appendChild(error);
    }
    const log = document.createElement("pre");
    log.className = "job-log";
    log.textContent = (job.logs || []).join("\n") || "暂无日志";
    detail.appendChild(log);
    if (job.status === "succeeded") renderDownloads(job);
  }

  function renderDownloads(job) {
    const archive = document.createElement("a");
    archive.href = `/api/jobs/${encodeURIComponent(job.id)}/archive`;
    archive.download = `job_${job.id}.zip`;
    archive.textContent = "下载完整 ZIP";
    const browse = document.createElement("button");
    browse.type = "button";
    browse.textContent = "浏览提取结果";
    browse.addEventListener("click", async () => {
      await loadRuns();
      runSelect.value = job.run_name;
      runSelect.dispatchEvent(new Event("change"));
      document.querySelector(".app").scrollIntoView({ behavior: "smooth" });
    });
    detail.append(archive, document.createTextNode(" "), browse);
  }

  async function cancelJob(jobId) {
    await request(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
    selectedJobId = jobId;
    await loadJobs();
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submitButton.disabled = true;
    message.className = "";
    message.textContent = "上传中…";
    const data = new FormData(form);
    for (const name of ["pp_structure_use_doc_orientation_classify", "pp_structure_use_doc_unwarping", "pp_structure_use_textline_orientation"]) {
      data.set(name, String(form.elements[name].checked));
    }
    try {
      const job = await request("/api/jobs", { method: "POST", body: data });
      keyInput.value = "";
      selectedJobId = job.id;
      message.textContent = `任务 ${job.id.slice(0, 8)} 已创建`;
      await loadJobs();
    } catch (error) {
      message.className = "error-text";
      message.textContent = error.message;
    } finally {
      submitButton.disabled = false;
    }
  });

  function showError(error) {
    message.className = "error-text";
    message.textContent = error.message;
  }

  document.querySelector("#jobsRefresh").addEventListener("click", () => loadJobs().catch(showError));
  Promise.all([loadGpus(), loadJobs()]).catch(showError);
  window.setInterval(() => {
    if (jobs.some((job) => ["queued", "running"].includes(job.status))) loadJobs().catch(showError);
  }, 2000);
})();
