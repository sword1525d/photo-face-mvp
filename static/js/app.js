/* ==========================================================================
   Photo Face MVP — JavaScript puro (sem frameworks)
   Responsabilidades:
     1. flashes / painéis / slug automático / confirmações
     2. upload em lote com drag & drop e barra de progresso
     3. busca por selfie com estados de carregamento e galeria de resultados
     4. lightbox (imagem ampliada em modal)
   ========================================================================== */

(function () {
  "use strict";

  const $$ = (selector, scope) => Array.from((scope || document).querySelectorAll(selector));
  const $ = (selector, scope) => (scope || document).querySelector(selector);

  /* ------------------------------------------------------------ helpers */

  function slugify(value) {
    return String(value || "")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80);
  }

  function humanSize(bytes) {
    if (!bytes) return "";
    const mb = bytes / (1024 * 1024);
    return mb >= 1 ? mb.toFixed(1) + " MB" : Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  function showError(box, message) {
    if (!box) return;
    box.textContent = message;
    box.hidden = false;
  }

  function hideError(box) {
    if (!box) return;
    box.hidden = true;
    box.textContent = "";
  }

  function pluralize(count, singular, plural) {
    return count === 1 ? singular : plural || singular + "s";
  }

  /* ------------------------------------------------------------- flashes */

  function initFlashes() {
    $$(".flash").forEach((flash) => {
      const close = () => {
        flash.style.transition = "opacity .25s ease";
        flash.style.opacity = "0";
        setTimeout(() => flash.remove(), 250);
      };
      $(".flash-close", flash)?.addEventListener("click", close);
      if (flash.classList.contains("flash-success")) setTimeout(close, 6000);
    });
  }

  /* ------------------------------------------------- painéis e slug auto */

  function initPanels() {
    $$("[data-toggle-target]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = $(button.dataset.toggleTarget);
        if (!target) return;
        target.hidden = !target.hidden;
        if (!target.hidden) $("input, textarea", target)?.focus();
      });
    });

    const nameInput = $("#event-name");
    const slugPreview = $("#slug-preview");
    if (nameInput && slugPreview) {
      const update = () => {
        const slug = slugify(nameInput.value) || "…";
        slugPreview.textContent = "/evento/" + slug;
      };
      nameInput.addEventListener("input", update);
      update();
    }
  }

  /* ------------------------------------------------------- links "POST" */

  function postForm(url) {
    const form = document.createElement("form");
    form.method = "post";
    form.action = url;
    document.body.appendChild(form);
    form.submit();
  }

  function initDestructiveActions() {
    $$(".js-delete-event").forEach((button) => {
      button.addEventListener("click", () => {
        const name = button.dataset.name || "este evento";
        if (window.confirm("Excluir " + name + " e TODAS as fotos do evento? Esta ação não pode ser desfeita.")) {
          postForm(button.dataset.url);
        }
      });
    });
  }

  /* ------------------------------------------------------ lightbox modal */

  function initLightbox() {
    const lightbox = $("#lightbox");
    if (!lightbox) return;
    const image = $(".lightbox-image", lightbox);
    const caption = $(".lightbox-caption", lightbox);

    const close = () => {
      lightbox.hidden = true;
      image.src = "";
    };

    const open = (src, text) => {
      image.src = src;
      caption.textContent = text || "";
      lightbox.hidden = false;
    };

    document.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-full]");
      if (trigger) {
        event.preventDefault();
        open(trigger.dataset.full, trigger.dataset.caption);
        return;
      }
      if (event.target === lightbox || event.target.closest(".lightbox-close")) close();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !lightbox.hidden) close();
    });
  }

  /* ------------------------------------------------------ upload em lote */

  // Formatos aceitos: imagens comuns (inclusive HEIC de celular), RAW de câmera
  // e ZIP com várias fotos. O servidor valida o conteúdo, não a extensão.
  const UPLOAD_ACCEPT = /\.(jpe?g|png|webp|heic|heif|avif|nef|nrw|cr2|cr3|arw|dng|orf|raf|rw2|pef|srw|sr2|3fr|erf|mrw|x3f|kdc|dcr|mef|iiq|rwl|zip)$/i;

  function initUploader() {
    const uploader = $("#uploader");
    if (!uploader) return;

    const url = uploader.dataset.uploadUrl;
    const dropzone = $("#dropzone", uploader);
    const input = $("#file-input", uploader);
    const progress = $("#upload-progress", uploader);
    const bar = $("#progress-bar", uploader);
    const label = $("#progress-label", uploader);
    const percent = $("#progress-percent", uploader);
    const detail = $("#progress-detail", uploader);
    const errorList = $("#upload-errors", uploader);
    const grid = $("#photo-grid");
    const emptyState = $("#gallery-empty");
    const countBadge = $("#photo-count");

    let running = false;

    const setProgress = (done, total, text) => {
      const pct = total ? Math.round((done / total) * 100) : 0;
      progress.hidden = false;
      bar.style.width = pct + "%";
      percent.textContent = pct + "%";
      label.textContent = "Processando " + done + " / " + total + " fotos";
      if (text !== undefined) detail.textContent = text;
    };

    const updateStats = (stats) => {
      if (!stats) return;
      Object.keys(stats).forEach((key) => {
        const node = $('[data-stat="' + key + '"]');
        if (node) node.textContent = stats[key];
      });
      if (countBadge) countBadge.textContent = stats.total + " " + pluralize(stats.total, "foto");
      if (emptyState) emptyState.hidden = stats.total > 0;
    };

    const buildCard = (item) => {
      const figure = document.createElement("figure");
      figure.className = "photo-card";
      figure.dataset.photoId = item.photo_id;

      const thumb = document.createElement("button");
      thumb.type = "button";
      thumb.className = "photo-thumb";
      if (item.original_url) thumb.dataset.full = item.original_url;
      thumb.dataset.caption = item.filename || "";
      if (item.thumbnail_url) {
        const img = document.createElement("img");
        img.src = item.thumbnail_url;
        img.alt = item.filename || "";
        img.loading = "lazy";
        thumb.appendChild(img);
      } else {
        const span = document.createElement("span");
        span.className = "thumb-fallback";
        span.textContent = "sem prévia";
        thumb.appendChild(span);
      }

      const meta = document.createElement("figcaption");
      meta.className = "photo-meta";
      const status = document.createElement("span");
      status.className = "status status-" + item.status;
      status.textContent = item.status_label || item.status;
      const faces = document.createElement("span");
      faces.className = "faces-count";
      faces.textContent = (item.faces_count || 0) + " " + pluralize(item.faces_count || 0, "rosto");
      meta.append(status, faces);
      if (item.raw_url) {
        const rawTag = document.createElement("span");
        rawTag.className = "raw-tag";
        rawTag.title = "Arquivo RAW original preservado";
        rawTag.textContent = "RAW";
        meta.appendChild(rawTag);
      }

      const actions = document.createElement("div");
      actions.className = "photo-actions";
      actions.innerHTML =
        (item.raw_url
          ? '<a class="icon-btn" href="' + item.raw_url + '" download title="Baixar o RAW original">&#10515;</a>'
          : "") +
        '<button class="icon-btn js-reprocess-photo" type="button" title="Reprocessar">&#8635;</button>' +
        '<button class="icon-btn icon-btn-danger js-delete-photo" type="button" title="Excluir foto">&#128465;</button>';
      $$("button", actions).forEach((button) => (button.dataset.photoId = item.photo_id));

      figure.append(thumb, meta, actions);
      return figure;
    };

    const addIssue = (text, kind) => {
      const li = document.createElement("li");
      li.textContent = text;
      if (kind === "warn") li.className = "upload-warn";
      errorList.appendChild(li);
    };

    async function uploadOne(file, index, total) {
      const body = new FormData();
      body.append("files", file);
      const isArchive = /\.zip$/i.test(file.name);
      setProgress(index, total, "Enviando " + file.name + "…");

      try {
        const response = await fetch(url, {
          method: "POST",
          body: body,
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        const data = await response.json().catch(() => null);

        if (!data) throw new Error("Resposta inválida do servidor.");
        if (data.error) throw new Error(data.error);

        const items = data.results || [];
        const skipped = data.skipped || [];
        skipped.forEach((entry) => addIssue(entry.filename + ": " + entry.reason, "warn"));

        if (!items.length && !skipped.length) {
          throw new Error("Não foi possível processar este arquivo.");
        }

        let processed = 0;
        let faceCount = 0;
        items.forEach((item) => {
          if (item.photo_id && grid) grid.prepend(buildCard(item));
          if (item.ok) {
            processed += 1;
            faceCount += item.faces_count || 0;
          } else {
            addIssue((item.filename || file.name) + ": " + (item.error || "erro no processamento."), "error");
          }
        });

        if (processed) {
          detail.textContent = isArchive
            ? file.name + ": " + processed + " foto(s) adicionada(s), " + faceCount + " rosto(s) detectado(s)."
            : file.name + ": " +
              (faceCount
                ? faceCount + " " + pluralize(faceCount, "rosto") + " detectado" + (faceCount === 1 ? "" : "s")
                : "nenhum rosto detectado.");
        } else {
          detail.textContent = file.name + ": nenhuma foto válida encontrada.";
        }
        updateStats(data.stats);
        return processed;
      } catch (error) {
        addIssue(file.name + ": " + error.message, "error");
        detail.textContent = file.name + ": " + error.message;
        return 0;
      }
    }

    async function run(files) {
      if (running || !files.length) return;
      running = true;
      errorList.innerHTML = "";
      detail.textContent = "";
      setProgress(0, files.length, "Aguarde enquanto processamos as fotos…");

      let added = 0;
      for (let index = 0; index < files.length; index += 1) {
        added += await uploadOne(files[index], index, files.length);
      }

      setProgress(
        files.length,
        files.length,
        "Concluído: " + added + " foto(s) adicionada(s) de " + files.length + " arquivo(s) enviado(s)."
      );
      running = false;
    }

    dropzone.addEventListener("click", () => input.click());
    dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
    input.addEventListener("change", () => {
      run(Array.from(input.files || []));
      input.value = "";
    });

    ["dragenter", "dragover"].forEach((type) =>
      dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.add("dragover");
      })
    );
    ["dragleave", "drop"].forEach((type) =>
      dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.remove("dragover");
      })
    );
    dropzone.addEventListener("drop", (event) => {
      const dropped = Array.from(event.dataTransfer?.files || []);
      const files = dropped.filter(
        (file) => UPLOAD_ACCEPT.test(file.name) || /^image\//i.test(file.type || "")
      );
      if (!files.length) {
        addIssue("Nenhum arquivo válido foi solto aqui (use JPG, PNG, WEBP, HEIC, NEF ou ZIP).", "error");
        return;
      }
      run(files);
    });

    // Excluir / reprocessar (delegação: vale também para cards criados depois).
    grid?.addEventListener("click", async (event) => {
      const card = event.target.closest(".photo-card");
      if (!card) return;
      const photoId = card.dataset.photoId;

      if (event.target.closest(".js-delete-photo")) {
        if (!window.confirm("Excluir esta foto do evento?")) return;
        const urlTemplate = grid.dataset.deleteUrl; // .../admin/photo/0/delete
        try {
          const response = await fetch(urlTemplate.replace("/0/", "/" + photoId + "/"), {
            method: "POST",
            headers: { "X-Requested-With": "XMLHttpRequest" },
          });
          const data = await response.json().catch(() => null);
          if (!response.ok || !data || !data.ok) throw new Error((data && data.error) || "Falha ao excluir.");
          card.remove();
          updateStats(data.stats);
        } catch (error) {
          window.alert(error.message);
        }
        return;
      }

      if (event.target.closest(".js-reprocess-photo")) {
        const urlTemplate = grid.dataset.reprocessUrl;
        try {
          detail.textContent = "Reprocessando a foto #" + photoId + "…";
          const response = await fetch(urlTemplate.replace("/0/", "/" + photoId + "/"), {
            method: "POST",
            headers: { "X-Requested-With": "XMLHttpRequest" },
          });
          const data = await response.json().catch(() => null);
          if (!response.ok || !data) throw new Error((data && data.error) || "Falha ao reprocessar.");
          updateStats(data.stats);
          window.location.reload();
        } catch (error) {
          window.alert(error.message);
        }
      }
    });
  }

  /* --------------------------------------------------------- busca facial */

  function initSelfieSearch() {
    const section = $("#search-section");
    if (!section) return;

    const searchUrl = section.dataset.searchUrl;
    const showSimilarity = section.dataset.showSimilarity === "true";

    const form = $("#selfie-form");
    const input = $("#selfie-input");
    const drop = $("#selfie-drop");
    const preview = $("#selfie-preview");
    const previewThumb = $("#selfie-thumb");
    const previewName = $("#selfie-name");
    const changeButton = $("#selfie-change");
    const consent = $("#consent");
    const submit = $("#selfie-submit");
    const errorBox = $("#search-error");
    const overlay = $("#loading-overlay");
    const step = $("#loading-step");
    const hint = $("#loading-hint");
    const results = $("#results");

    let currentFile = null;
    let busy = false;

    const syncConsent = () => {
      // Só cuida do estado do botão: esconder a mensagem de erro aqui apagaria
      // o aviso logo depois de exibi-lo (o `finally` da busca chama isto).
      if (submit) submit.disabled = !consent.checked || busy;
    };

    const askConsent = () => {
      showError(errorBox, "Marque a caixa de consentimento para enviarmos sua selfie.");
      consent.focus();
      consent.parentElement?.animate(
        [{ transform: "translateX(0)" }, { transform: "translateX(-4px)" }, { transform: "translateX(4px)" }, { transform: "translateX(0)" }],
        { duration: 260 }
      );
    };

    const setPreview = (file) => {
      currentFile = file;
      previewThumb.src = URL.createObjectURL(file);
      previewName.textContent = file.name + (file.size ? " · " + humanSize(file.size) : "");
      preview.hidden = false;
      drop.hidden = true;
    };

    const resetSelfie = () => {
      currentFile = null;
      if (input) input.value = "";
      preview.hidden = true;
      drop.hidden = false;
    };

    const openPicker = () => {
      if (!consent.checked) {
        askConsent();
        return;
      }
      input.click();
    };

    drop?.addEventListener("click", openPicker);
    changeButton?.addEventListener("click", () => {
      resetSelfie();
      openPicker();
    });

    consent?.addEventListener("change", () => {
      // Marcar o consentimento limpa um aviso de "falta consentimento".
      if (consent.checked && /consentimento/i.test(errorBox.textContent || "")) {
        hideError(errorBox);
      }
      syncConsent();
    });

    input?.addEventListener("change", () => {
      const file = (input.files || [])[0];
      if (!file) return;
      hideError(errorBox);
      setPreview(file);
      submitSearch(file);
    });

    async function submitSearch(file) {
      if (busy) return;
      if (!consent.checked) {
        askConsent();
        return;
      }

      busy = true;
      submit.disabled = true;
      hideError(errorBox);
      results.innerHTML = "";
      overlay.hidden = false;
      step.textContent = "Analisando sua selfie...";
      hint.textContent = "Isso leva apenas alguns segundos.";

      const searchingTimer = setTimeout(() => {
        step.textContent = "Procurando suas fotos...";
        hint.textContent = "Comparando seu rosto com as fotos do evento.";
      }, 1200);

      try {
        const body = new FormData();
        body.append("selfie", file);
        body.append("consent", "1");

        const response = await fetch(searchUrl, {
          method: "POST",
          body: body,
          headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" },
        });
        const data = await response.json().catch(() => null);

        if (!response.ok || !data || data.ok === false) {
          throw new Error((data && data.error) || "Não foi possível concluir a busca. Tente novamente.");
        }
        renderResults(data);
      } catch (error) {
        showError(errorBox, error.message);
        results.innerHTML = "";
      } finally {
        clearTimeout(searchingTimer);
        overlay.hidden = true;
        busy = false;
        syncConsent();
        section.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }

    function renderResults(data) {
      const total = data.count || 0;
      const head = document.createElement("div");
      head.className = "results-head";
      const title = document.createElement("p");
      title.className = "count";
      title.textContent = total
        ? "Encontramos " + total + " " + pluralize(total, "foto") + " " + pluralize(total, "sua", "suas")
        : "Não encontramos fotos correspondentes";
      const meta = document.createElement("p");
      meta.className = "muted small";
      meta.textContent = "Busca concluída em " + (data.elapsed ?? 0) + "s";
      head.append(title, meta);
      results.appendChild(head);

      if (!total) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.innerHTML =
          "<h2>Não encontramos fotos correspondentes</h2>" +
          "<p>Tente outra selfie com o rosto bem visível, sem óculos escuros e com boa iluminação.</p>" +
          '<button type="button" class="btn btn-primary js-try-again">Enviar outra selfie</button>';
        results.appendChild(empty);
        $(".js-try-again", empty)?.addEventListener("click", () => {
          resetSelfie();
          openPicker();
        });
        return;
      }

      const grid = document.createElement("div");
      grid.className = "photo-grid";
      data.results.forEach((item, index) => {
        const figure = document.createElement("figure");
        figure.className = "photo-card";
        figure.dataset.photoId = item.photo_id;

        const thumb = document.createElement("button");
        thumb.type = "button";
        thumb.className = "photo-thumb";
        thumb.dataset.full = item.original_url || item.thumbnail_url || "";
        thumb.dataset.caption = item.filename || "Foto " + (index + 1);
        const img = document.createElement("img");
        img.src = item.thumbnail_url || item.original_url;
        img.alt = "Foto " + (index + 1);
        img.loading = "lazy";
        thumb.appendChild(img);
        figure.appendChild(thumb);

        if (showSimilarity) {
          const meta = document.createElement("figcaption");
          meta.className = "photo-meta";
          const similarity = document.createElement("span");
          similarity.className = "similarity";
          similarity.textContent = Math.round((item.similarity || 0) * 100) + "% de similaridade";
          meta.appendChild(similarity);
          figure.appendChild(meta);
          console.debug("[photo-face] resultado", item.photo_id, "similaridade", item.similarity);
        }
        grid.appendChild(figure);
      });
      results.appendChild(grid);
    }

    // Progressive enhancement: sem JS o form faz POST normal e renderiza results.html.
    form?.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!currentFile) {
        openPicker();
        return;
      }
      submitSearch(currentFile);
    });

    syncConsent();
  }

  /* -------------------------------------------------------------- boot */

  document.addEventListener("DOMContentLoaded", () => {
    initFlashes();
    initPanels();
    initDestructiveActions();
    initLightbox();
    initUploader();
    initSelfieSearch();
  });
})();
