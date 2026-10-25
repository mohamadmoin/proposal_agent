// Live progress and results for one proposal. Polls the status every 2 s until the job is finished,
// then renders one tab per section. Built with DOM nodes only (no innerHTML), so model text cannot inject HTML.
(function () {
  "use strict";

  var root = document.getElementById("proposal");
  if (!root) return;
  var statusUrl = root.dataset.statusUrl;
  var lang = root.dataset.language;
  var rtl = lang === "fa";
  var FINISHED = ["done", "done_with_errors", "failed"];
  var STATUS_TEXT = { queued: "Queued", running: "Running", done: "Done", done_with_errors: "Done with errors", failed: "Failed" };
  var FLAG_TEXT = {
    numbers_unverified: "numbers unverified",
    partly_similar: "partly similar",
    sample_size_missing: "sample size not stated",
    aims_count_mismatch: "counts do not match",
    finding_replaced: "finding replaced by evidence",
    evidence_invalid: "evidence invalid",
    incomplete: "incomplete",
    failed: "failed"
  };

  // --- small DOM helpers ---
  function el(tag, attrs) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === "text") node.textContent = attrs[key];
      else if (key === "hidden") node.hidden = attrs[key];
      else if (attrs[key] !== null && attrs[key] !== undefined) node.setAttribute(key, attrs[key]);
    });
    for (var i = 2; i < arguments.length; i++) {
      var child = arguments[i];
      if (child === null || child === undefined || child === false) continue;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }
  function contentAttrs(extra) {
    var attrs = { lang: lang, dir: rtl ? "rtl" : "ltr" };
    Object.keys(extra || {}).forEach(function (k) { attrs[k] = extra[k]; });
    return attrs;
  }
  function flags(list) {
    var span = el("span");
    (list || []).forEach(function (flag) {
      var name = flag.indexOf("duplicate_of:") === 0 ? "duplicate" : (FLAG_TEXT[flag] || flag.replace(/_/g, " "));
      span.appendChild(el("span", { "class": "flag", text: name }));
    });
    return span;
  }

  // Text with "[1, 2]" citations turned into links to the reference list.
  function citedText(text) {
    var fragment = document.createDocumentFragment();
    var pattern = /\[(\d+(?:, \d+)*)\]/g;
    var last = 0;
    var match;
    while ((match = pattern.exec(text)) !== null) {
      fragment.appendChild(document.createTextNode(text.slice(last, match.index)));
      var bracket = el("span", { "class": "citation", dir: "ltr" }, "[");
      match[1].split(", ").forEach(function (number, i) {
        if (i) bracket.appendChild(document.createTextNode(", "));
        bracket.appendChild(el("a", { href: "#ref-" + number, "class": "cite", "aria-label": "Reference " + number, text: number }));
      });
      bracket.appendChild(document.createTextNode("]"));
      fragment.appendChild(bracket);
      last = pattern.lastIndex;
    }
    fragment.appendChild(document.createTextNode(text.slice(last)));
    return fragment;
  }
  function paragraphs(text) {
    var box = el("div", contentAttrs({ "class": "prose" }));
    (text || "").split(/\n+/).forEach(function (block) {
      if (block.trim()) box.appendChild(el("p", {}, citedText(block.trim())));
    });
    return box;
  }
  function failedBox(section) {
    return el("div", { "class": "error", role: "alert" }, "This section failed: " + (section.error || "unknown reason"));
  }

  // --- progress ---
  function renderProgress(data) {
    var badge = document.getElementById("status-badge");
    badge.textContent = STATUS_TEXT[data.status] || data.status;
    badge.className = "badge status-" + data.status;
    var list = document.getElementById("steps");
    list.textContent = "";
    var latest = {};
    data.steps.forEach(function (step) {
      if (step.status !== "warning") latest[step.step] = step;
    });
    Object.keys(latest).forEach(function (key) {
      var step = latest[key];
      var item = el("li", { "class": step.status }, el("strong", { text: key.replace(/_/g, " ") }));
      if (step.message) item.appendChild(el("span", { "class": "msg", dir: "auto", text: step.message }));
      list.appendChild(item);
    });
    if (!data.steps.length) list.appendChild(el("li", { "class": "started", text: "Waiting for a worker…" }));
    if (data.error) list.appendChild(el("li", { "class": "failed", text: data.error }));
    var warnings = data.warnings || [];
    document.getElementById("warnings").hidden = !warnings.length;
    var wl = document.getElementById("warning-list");
    wl.textContent = "";
    warnings.forEach(function (w) { wl.appendChild(el("li", { dir: "auto", text: w.message })); });
  }

  // --- section panels ---
  function introPanel(section) {
    return paragraphs(section.text);
  }

  function aimsPanel(section) {
    var box = el("div", contentAttrs());
    box.appendChild(el("h3", { text: rtl ? "هدف اصلی" : "Main aim" }));
    box.appendChild(el("p", {}, citedText(section.main_aim || "")));
    var groups = [
      [rtl ? "اهداف فرعی" : "Secondary aims", section.secondary_aims || []],
      [rtl ? "فرضیه‌ها" : "Hypotheses", (section.hypotheses || []).map(function (h) {
        return h.applicable ? h.text : (rtl ? "کاربرد ندارد: " : "Not applicable: ") + h.text;
      })],
      [rtl ? "سؤالات پژوهش" : "Research questions", section.questions || []],
      [rtl ? "اهداف کاربردی" : "Practical aims", section.practical_aims || []]
    ];
    groups.forEach(function (group) {
      if (!group[1].length) return;
      box.appendChild(el("h3", { text: group[0] }));
      var ol = el("ol");
      group[1].forEach(function (item) { ol.appendChild(el("li", {}, citedText(item))); });
      box.appendChild(ol);
    });
    if ((section.flags || []).length) box.appendChild(flags(section.flags));
    return box;
  }

  function backgroundPanel(section, data) {
    var box = el("div");
    (section.paragraphs || []).forEach(function (p) {
      var block = paragraphs(p.text);
      if ((p.flags || []).length) block.appendChild(flags(p.flags));
      box.appendChild(block);
    });
    box.appendChild(el("h3", { text: "Selected studies" }));
    var table = el("table", {},
      el("thead", {}, el("tr", {}, el("th", { text: "Token" }), el("th", { text: "Study" }), el("th", { text: "Year" }),
        el("th", { text: "Aim" }), el("th", { text: "Population" }), el("th", { text: "Same design" }), el("th", { text: "Score" }))));
    var body = el("tbody");
    (section.papers || []).forEach(function (paper) {
      body.appendChild(el("tr", {},
        el("td", { text: paper.token }),
        el("td", {}, el("a", { href: "https://pubmed.ncbi.nlm.nih.gov/" + paper.pmid + "/", target: "_blank", rel: "noopener", text: paper.title }), flags(paper.flags)),
        el("td", { "class": "num", text: paper.year || "" }),
        el("td", { "class": "num", text: String(paper.aim_similarity) + "/3" }),
        el("td", { "class": "num", text: String(paper.population_similarity) + "/3" }),
        el("td", { text: paper.design_match ? "yes" : "no" }),
        el("td", { "class": "num", text: String(paper.score) })));
    });
    table.appendChild(body);
    box.appendChild(el("div", { "class": "table-wrap" }, table));
    return box;
  }

  function methodsPanel(section) {
    var box = el("div");
    var design = section.design || {};
    var decision = el("div", { "class": "card" },
      el("h3", { text: "Study design" }),
      el("p", {}, el("strong", { text: (design.name && design.name[lang]) || design.label || "" }),
        " — " + (design.source === "user" ? "chosen by you" : design.source === "router" ? "chosen by the design questionnaire" : "from the study profile")));
    if (design.rationale) decision.appendChild(el("p", { dir: "auto", text: design.rationale }));
    if ((design.missing_information || []).length) {
      var missing = el("ul");
      design.missing_information.forEach(function (m) { missing.appendChild(el("li", { dir: "auto", text: m })); });
      decision.appendChild(el("p", { text: "Details to add to make the choice certain:" }));
      decision.appendChild(missing);
    }
    if ((design.answers || []).length) {
      var answers = el("ol");
      design.answers.forEach(function (a) { answers.appendChild(el("li", { dir: "auto", text: a.answer })); });
      decision.appendChild(el("details", {}, el("summary", { text: "The 12 design questions, answered" }), answers));
    }
    box.appendChild(decision);

    var size = section.sample_size;
    if (size) {
      box.appendChild(el("div", { "class": "card" },
        el("h3", { text: "Sample size (computed by code)" }),
        el("p", {}, "Final sample size: ", el("strong", { text: String(size.final_total) }), " · formula: " + size.formula),
        el("pre", contentAttrs({ "class": "calc" }), citedText(size.worked))));
    } else if (section.sample_size_error) {
      box.appendChild(el("div", { "class": "error" }, "The sample size could not be computed: " + section.sample_size_error));
    }
    (section.subsections || []).forEach(function (sub) {
      box.appendChild(el("h3", contentAttrs({ text: sub.title })));
      if (sub.text) box.appendChild(paragraphs(sub.text));
      else box.appendChild(el("p", { "class": "error", text: "Not written: " + (sub.error || "failed") }));
      if ((sub.flags || []).length) box.appendChild(flags(sub.flags));
    });
    return box;
  }

  function referencesPanel(data) {
    var ol = el("ol", { "class": "references", dir: "ltr", lang: "en" });
    (data.references || []).forEach(function (ref) {
      var item = el("li", { id: "ref-" + ref.number, value: String(ref.number), tabindex: "-1" }, ref.text + " ");
      if (ref.url) item.appendChild(el("a", { href: ref.url, target: "_blank", rel: "noopener", text: "link" }));
      ol.appendChild(item);
    });
    if (!(data.references || []).length) return el("p", { text: "No references." });
    return ol;
  }

  function howPanel(data) {
    var trace = data.trace || {};
    var box = el("div");
    box.appendChild(el("p", { text: "Everything the app did for this proposal: the searches, the papers kept at each stage and every model call." }));
    var stages = trace.stages || {};
    box.appendChild(el("h3", { text: "Papers at each stage" }));
    box.appendChild(el("ul", {},
      el("li", { text: "Background records retrieved: " + (stages.retrieved || 0) }),
      el("li", { text: "Screened by the model: " + (stages.screened || 0) }),
      el("li", { text: "Selected as strictly similar: " + (stages.selected || 0) }),
      el("li", { text: "Methods evidence papers: " + (stages.methods_papers || 0) }),
      el("li", { text: "Introduction sources: " + (stages.intro_sources || 0) })));

    box.appendChild(el("h3", { text: "PubMed searches" }));
    var searches = el("tbody");
    (trace.searches || []).forEach(function (s) {
      searches.appendChild(el("tr", {}, el("td", { text: s.step }), el("td", { text: s.lane }),
        el("td", { dir: "ltr" }, el("code", { text: s.query })), el("td", { "class": "num", text: s.error ? "error" : String(s.count) })));
    });
    box.appendChild(el("div", { "class": "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", { text: "Step" }), el("th", { text: "Lane" }), el("th", { text: "Query" }), el("th", { text: "Hits" }))),
      searches)));

    var usage = data.usage || {};
    box.appendChild(el("h3", { text: "Model calls" }));
    var summary = (usage.calls || 0) + " calls · " + (usage.prompt_tokens || 0) + " input tokens · " + (usage.completion_tokens || 0) + " output tokens";
    if (trace.cost_shown && usage.cost_usd !== null && usage.cost_usd !== undefined) summary += " · cost US$" + usage.cost_usd;
    box.appendChild(el("p", { text: summary }));
    var calls = el("tbody");
    (trace.calls || []).forEach(function (c) {
      calls.appendChild(el("tr", {}, el("td", { text: c.step }), el("td", { text: c.model }), el("td", { text: c.status }),
        el("td", { "class": "num", text: String(c.prompt_tokens) }), el("td", { "class": "num", text: String(c.completion_tokens) }),
        el("td", { "class": "num", text: String(c.latency_ms) })));
    });
    box.appendChild(el("div", { "class": "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", { text: "Step" }), el("th", { text: "Model" }), el("th", { text: "Status" }),
        el("th", { text: "In" }), el("th", { text: "Out" }), el("th", { text: "ms" }))), calls)));
    return box;
  }

  // --- tabs ---
  function selectTab(id) {
    document.querySelectorAll("#tablist [role=tab]").forEach(function (tab) {
      var on = tab.id === "tab-" + id;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
      document.getElementById("panel-" + tab.id.slice(4)).hidden = !on;
    });
  }

  function renderResults(data) {
    var sections = data.sections || {};
    var tabs = [];
    var titles = { introduction: rtl ? "مقدمه" : "Introduction", aims: rtl ? "اهداف" : "Aims",
      background: rtl ? "پیشینه" : "Background", methods: rtl ? "روش اجرا" : "Methods" };
    ["introduction", "aims", "background", "methods"].forEach(function (key) {
      var section = sections[key];
      if (!section) return;
      var panel = section.status !== "done" ? failedBox(section)
        : key === "introduction" ? introPanel(section)
        : key === "aims" ? aimsPanel(section)
        : key === "background" ? backgroundPanel(section, data)
        : methodsPanel(section);
      tabs.push([key, titles[key] + (section.status !== "done" ? " (failed)" : ""), panel]);
    });
    tabs.push(["references", rtl ? "منابع" : "References", referencesPanel(data)]);
    tabs.push(["how", "How this was made", howPanel(data)]);

    var tablist = document.getElementById("tablist");
    var panels = document.getElementById("panels");
    tablist.textContent = "";
    panels.textContent = "";
    tabs.forEach(function (entry) {
      var tab = el("button", { type: "button", role: "tab", id: "tab-" + entry[0], "aria-controls": "panel-" + entry[0], text: entry[1] });
      tab.addEventListener("click", function () { selectTab(entry[0]); });
      tablist.appendChild(tab);
      panels.appendChild(el("div", { role: "tabpanel", id: "panel-" + entry[0], "aria-labelledby": "tab-" + entry[0], tabindex: "0" }, entry[2]));
    });
    tablist.addEventListener("keydown", function (event) {
      var all = Array.prototype.slice.call(tablist.querySelectorAll("[role=tab]"));
      var index = all.indexOf(document.activeElement);
      if (index < 0) return;
      var next = event.key === "ArrowRight" ? index + 1 : event.key === "ArrowLeft" ? index - 1 : null;
      if (next === null) return;
      next = (next + all.length) % all.length;
      all[next].focus();
      selectTab(all[next].id.slice(4));
    });
    // A citation jumps to its reference.
    panels.addEventListener("click", function (event) {
      var link = event.target.closest("a.cite");
      if (!link) return;
      event.preventDefault();
      selectTab("references");
      var target = document.querySelector(link.getAttribute("href"));
      if (target) { target.scrollIntoView({ behavior: "smooth", block: "center" }); target.focus(); history.replaceState(null, "", link.getAttribute("href")); }
    });
    // A link such as /p/<id>#tab-methods opens that tab.
    var fromHash = location.hash.indexOf("#tab-") === 0 ? location.hash.slice(5) : "";
    var known = tabs.some(function (entry) { return entry[0] === fromHash; });
    selectTab(known ? fromHash : tabs[0][0]);
    document.getElementById("results").hidden = false;
    var docx = document.getElementById("docx-link");
    docx.hidden = !(data.status === "done" || data.status === "done_with_errors");
  }

  // --- polling ---
  function poll() {
    fetch(statusUrl, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        renderProgress(data);
        if (FINISHED.indexOf(data.status) >= 0) {
          if (data.sections) {
            renderResults(data);
            document.getElementById("progress-details").open = false;  // the results matter now
          }
        } else {
          setTimeout(poll, 2000);
        }
      })
      .catch(function () { setTimeout(poll, 4000); });
  }
  poll();
})();
