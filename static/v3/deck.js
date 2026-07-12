(function (root) {
  "use strict";

  const SESSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
  const FRAME_PATTERN = /^(0|[1-9]\d*):(0|[1-9]\d*):([A-Za-z0-9_-]{1,64})$/;
  const MAX_FRAMES = 512;

  function parseDeckQuery(search) {
    const params = new URLSearchParams(search || "");
    const sessionValues = params.getAll("session_id");

    for (const key of params.keys()) {
      if (key !== "session_id" && key !== "frame") {
        return { ok: false, error: `Unsupported query parameter: ${key}` };
      }
    }

    if (sessionValues.length !== 1 || !SESSION_PATTERN.test(sessionValues[0])) {
      return { ok: false, error: "session_id is missing or invalid" };
    }

    const rawFrames = params.getAll("frame");
    if (rawFrames.length > MAX_FRAMES) {
      return { ok: false, error: `Deck exceeds the ${MAX_FRAMES}-frame limit` };
    }

    const frames = [];
    for (const rawFrame of rawFrames) {
      const match = FRAME_PATTERN.exec(rawFrame);
      if (!match) {
        return { ok: false, error: "A frame entry is invalid" };
      }

      const slideIndex = Number(match[1]);
      const buildIndex = Number(match[2]);
      if (!Number.isSafeInteger(slideIndex) || !Number.isSafeInteger(buildIndex)) {
        return { ok: false, error: "A frame index is outside the supported range" };
      }

      frames.push(Object.freeze({
        slideIndex,
        buildIndex,
        assetId: match[3],
      }));
    }

    return Object.freeze({
      ok: true,
      sessionId: sessionValues[0],
      frames: Object.freeze(frames),
    });
  }

  function assetUrl(sessionId, assetId) {
    if (!SESSION_PATTERN.test(sessionId) || !/^[A-Za-z0-9_-]{1,64}$/.test(assetId)) {
      throw new TypeError("Invalid deck asset reference");
    }
    return `/sessions/${encodeURIComponent(sessionId)}/assets/${encodeURIComponent(assetId)}`;
  }

  function navigationIndex(currentIndex, command, frameCount) {
    if (!Number.isInteger(currentIndex) || !Number.isInteger(frameCount) || frameCount <= 0) {
      return 0;
    }
    if (command === "first") return 0;
    if (command === "last") return frameCount - 1;
    if (command === "next") return Math.min(currentIndex + 1, frameCount - 1);
    if (command === "previous") return Math.max(currentIndex - 1, 0);
    return Math.min(Math.max(currentIndex, 0), frameCount - 1);
  }

  function isFormControlOrEditable(target) {
    if (!target || typeof target !== "object") return false;
    const tagName = String(target.tagName || "").toUpperCase();
    if (tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT" || tagName === "BUTTON") {
      return true;
    }
    if (target.isContentEditable === true) return true;
    return typeof target.closest === "function" && Boolean(target.closest("[contenteditable='true']"));
  }

  const api = Object.freeze({
    MAX_FRAMES,
    parseDeckQuery,
    assetUrl,
    navigationIndex,
    isFormControlOrEditable,
  });

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }

  function initialize() {
    const documentRef = root.document;
    const image = documentRef.getElementById("frame-image");
    const stage = documentRef.getElementById("stage");
    const statePanel = documentRef.getElementById("state-panel");
    const stateKicker = documentRef.getElementById("state-kicker");
    const stateTitle = documentRef.getElementById("state-title");
    const stateDetail = documentRef.getElementById("state-detail");
    const slideProgress = documentRef.getElementById("slide-progress");
    const buildProgress = documentRef.getElementById("build-progress");
    const frameProgress = documentRef.getElementById("frame-progress");
    const previousButton = documentRef.getElementById("previous-button");
    const nextButton = documentRef.getElementById("next-button");

    let currentIndex = 0;
    let preloadedImage = null;
    const config = parseDeckQuery(root.location.search);

    function setProgressText(slideText, buildText, frameText) {
      slideProgress.textContent = slideText;
      buildProgress.textContent = buildText;
      frameProgress.textContent = frameText;
    }

    function showState(kind, title, detail) {
      image.hidden = true;
      statePanel.hidden = false;
      statePanel.className = kind === "error" ? "state-panel error" : "state-panel";
      stateKicker.textContent = kind === "error" ? "PLAYBACK ERROR" : "LUMERI DECK";
      stateTitle.textContent = title;
      stateDetail.textContent = detail;
    }

    if (!config.ok) {
      setProgressText("Slide —", "Build —", "Frame —");
      previousButton.disabled = true;
      nextButton.disabled = true;
      showState("error", "Invalid deck URL", config.error);
      return;
    }

    if (config.frames.length === 0) {
      setProgressText("Slide —", "Build —", "Frame 0 / 0");
      previousButton.disabled = true;
      nextButton.disabled = true;
      showState("empty", "No frames to present", "Render the deck and pass its frame assets in playback order.");
      return;
    }

    function preloadNextFrame() {
      preloadedImage = null;
      if (currentIndex + 1 >= config.frames.length) return;
      const nextFrame = config.frames[currentIndex + 1];
      const candidate = new root.Image();
      candidate.referrerPolicy = "no-referrer";
      candidate.decoding = "async";
      candidate.src = assetUrl(config.sessionId, nextFrame.assetId);
      preloadedImage = candidate;
    }

    function renderFrame() {
      const frame = config.frames[currentIndex];
      const url = assetUrl(config.sessionId, frame.assetId);

      setProgressText(
        `Slide ${frame.slideIndex + 1}`,
        `Build ${frame.buildIndex + 1}`,
        `Frame ${currentIndex + 1} / ${config.frames.length}`,
      );
      previousButton.disabled = currentIndex === 0;
      nextButton.disabled = currentIndex === config.frames.length - 1;
      image.alt = `Slide ${frame.slideIndex + 1}, build ${frame.buildIndex + 1}`;
      image.onload = function () {
        if (image.getAttribute("src") !== url) return;
        statePanel.hidden = true;
        image.hidden = false;
      };
      image.onerror = function () {
        if (image.getAttribute("src") !== url) return;
        showState("error", "Frame unavailable", `Could not load frame ${currentIndex + 1}.`);
      };
      image.hidden = true;
      statePanel.hidden = true;
      image.src = url;
      preloadNextFrame();
    }

    function navigate(command) {
      const nextIndex = navigationIndex(currentIndex, command, config.frames.length);
      if (nextIndex === currentIndex) return;
      currentIndex = nextIndex;
      renderFrame();
    }

    previousButton.addEventListener("click", function () {
      navigate("previous");
    });
    nextButton.addEventListener("click", function () {
      navigate("next");
    });
    stage.addEventListener("click", function () {
      navigate("next");
    });

    root.addEventListener("keydown", function (event) {
      const isSpace = event.key === " " || event.key === "Spacebar" || event.code === "Space";
      if (isSpace && isFormControlOrEditable(event.target)) return;

      let command = "";
      if (isSpace || event.key === "ArrowRight" || event.key === "PageDown") command = "next";
      else if (event.key === "ArrowLeft" || event.key === "PageUp") command = "previous";
      else if (event.key === "Home") command = "first";
      else if (event.key === "End") command = "last";
      if (!command) return;

      event.preventDefault();
      navigate(command);
    });

    renderFrame();
  }

  if (root.document) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
      initialize();
    }
  }
}(typeof globalThis !== "undefined" ? globalThis : this));
