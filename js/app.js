import { PLATFORM_LABEL, filterFiches, pageWindow, safeProfileUrl } from "./liste.js";

const PAGE_SIZE = 20;
const $ = (sel) => document.querySelector(sel);

const nf = new Intl.NumberFormat("fr-FR");

let all = [];
let page = 1;

function formatInt(value) {
  return nf.format(Number(value) || 0);
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = formatInt(value);
}



function fillSelect(id, pairs, allLabel) {
  const select = document.getElementById(id);
  const current = select.value;
  select.replaceChildren();
  const first = document.createElement("option");
  first.value = "";
  first.textContent = allLabel;
  select.append(first);
  pairs.forEach(([label, count]) => {
    const option = document.createElement("option");
    option.value = label;
    option.textContent = `${label} (${formatInt(count)})`;
    select.append(option);
  });
  const wanted = [...select.options].some((option) => option.value === current);
  if (wanted) select.value = current;
}

function readControls() {
  return {
    q: $("#q").value,
    category: $("#cat").value,
    platform: $("#net").value,
    sort: $("#sort").value || "count",
  };
}

function writeQuery() {
  const { q, category, platform, sort } = readControls();
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  if (category) params.set("cat", category);
  if (platform) params.set("net", platform);
  if (sort && sort !== "count") params.set("sort", sort);
  if (page > 1) params.set("page", String(page));
  const next = params.toString();
  const path = next ? `${location.pathname}?${next}` : location.pathname;
  history.replaceState(null, "", path + location.hash);
}

function readQuery() {
  const params = new URLSearchParams(location.search);
  if (params.get("q")) $("#q").value = params.get("q");
  if (params.get("sort")) $("#sort").value = params.get("sort");
  const asked = Number(params.get("page"));
  if (asked) page = asked;
  return {
    cat: params.get("cat") || "",
    net: params.get("net") || "",
  };
}

function showState(message) {
  const list = $("#list");
  list.replaceChildren();
  const p = document.createElement("p");
  p.className = "state";
  p.textContent = message;
  list.append(p);
  list.removeAttribute("aria-busy");
}

function renderRow(fiche) {
  const article = document.createElement("article");
  article.className = "fiche";

  const name = document.createElement("h3");
  name.textContent = fiche.name;

  const category = document.createElement("p");
  category.className = "fiche-cat";
  category.textContent = fiche.category;

  const network = document.createElement("p");
  network.className = "fiche-net";
  network.textContent = PLATFORM_LABEL[fiche.platform] || fiche.platform;

  const count = document.createElement("p");
  count.className = "fiche-count";
  const num = document.createElement("span");
  num.textContent = formatInt(fiche.count);
  const unit = document.createElement("span");
  unit.className = "unit";
  unit.textContent = "blocages";
  count.append(num, unit);

  const href = safeProfileUrl(fiche.url);
  let action;
  if (href) {
    action = document.createElement("a");
    action.className = "btn btn-red fiche-go";
    action.href = href;
    action.target = "_blank";
    action.rel = "noopener noreferrer";
    action.textContent = "Profil public";
    const hidden = document.createElement("span");
    hidden.className = "sr-only";
    hidden.textContent = `, ${fiche.name}, nouvelle fenêtre`;
    action.append(hidden);
  } else {
    action = document.createElement("p");
    action.className = "fiche-go";
    action.textContent = "Lien retiré";
  }

  const reason = document.createElement("p");
  reason.className = "fiche-reason";
  reason.textContent = fiche.reason || "Motif non inscrit.";

  article.append(name, category, network, count, action, reason);
  return article;
}

function render() {
  const controls = readControls();
  const rows = filterFiches(all, controls);
  const windowed = pageWindow(rows, page, PAGE_SIZE);
  page = windowed.page;

  $("#status").textContent = rows.length
    ? `${formatInt(rows.length)} fiches`
    : "Aucune fiche. Le tiroir ne connaît pas ce nom.";
  $("#page-label").textContent = rows.length
    ? `Page ${windowed.page} sur ${windowed.pages}`
    : "Page 0";
  $("#prev").disabled = !rows.length || windowed.page <= 1;
  $("#next").disabled = !rows.length || windowed.page >= windowed.pages;

  const list = $("#list");
  list.replaceChildren();
  if (!rows.length) {
    const p = document.createElement("p");
    p.className = "state";
    p.textContent = "Rien dans ce rayon. L'orthographe a peut-être été bloquée aussi.";
    list.append(p);
  } else {
    const frag = document.createDocumentFragment();
    windowed.slice.forEach((fiche) => frag.append(renderRow(fiche)));
    list.append(frag);
  }
  list.removeAttribute("aria-busy");
  writeQuery();
}

function bind() {
  $("#filters").addEventListener("submit", (event) => {
    event.preventDefault();
    page = 1;
    render();
  });

  $("#q").addEventListener("input", () => {
    page = 1;
    render();
  });

  ["cat", "net", "sort"].forEach((id) => {
    document.getElementById(id).addEventListener("change", () => {
      page = 1;
      render();
    });
  });

  $("#prev").addEventListener("click", () => {
    page -= 1;
    render();
    $("#list").scrollIntoView({ block: "nearest" });
  });

  $("#next").addEventListener("click", () => {
    page += 1;
    render();
    $("#list").scrollIntoView({ block: "nearest" });
  });
}

async function init() {
  const pending = readQuery();
  try {
    const response = await fetch("data/fiches.json");
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    all = Array.isArray(data.fiches) ? data.fiches : [];
    setText("total-blocks", data.total_blocks);
    setText("fiche-count", data.fiche_count);
    setText("name-count", data.name_count);
    setText("fiche-sum", data.fiche_count_sum);
    setText("historical", data.historical_base);

    const categories = (data.categories || []).map(([name, count]) => [name, count]);
    const platforms = (data.platforms || []).map(([name, count]) => [
      PLATFORM_LABEL[name] ? name : name,
      count,
    ]);
    fillSelect("cat", categories, "Tous");
    fillSelect(
      "net",
      platforms.map(([name, count]) => [name, count]),
      "Tous",
    );
    $("#net").querySelectorAll("option").forEach((option) => {
      if (!option.value) return;
      const label = PLATFORM_LABEL[option.value] || option.value;
      const count = option.textContent.match(/\(([^)]+)\)$/);
      option.textContent = count ? `${label} (${count[1]})` : label;
    });

    if (pending.cat && [...$("#cat").options].some((option) => option.value === pending.cat)) {
      $("#cat").value = pending.cat;
    }
    if (pending.net && [...$("#net").options].some((option) => option.value === pending.net)) {
      $("#net").value = pending.net;
    }

    bind();
    render();
  } catch {
    showState("Le tiroir est coincé. Le fichier des fiches n'est pas arrivé.");
    $("#status").textContent = "Erreur de chargement.";
  }
}

if ($("#list")) init();
