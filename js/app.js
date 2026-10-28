import { PLATFORM_LABEL, filterFiches, pageWindow, safeProfileUrl } from "./liste.js";

const SUB_KIND = { new: "Ajout", removal: "Retrait", update: "Mise à jour" };

const PAGE_SIZE = 20;
const $ = (sel) => document.querySelector(sel);

const nf = new Intl.NumberFormat("fr-FR");

const MONTHS = [
  "janvier", "février", "mars", "avril", "mai", "juin",
  "juillet", "août", "septembre", "octobre", "novembre", "décembre",
];

let all = [];
let page = 1;
let seenAsOf = null;
let seenFollowers = null;
let liveTimer = 0;

function formatInt(value) {
  return nf.format(Number(value) || 0);
}

const SHORT_MONTHS = [
  "janv.", "févr.", "mars", "avr.", "mai", "juin",
  "juil.", "août", "sept.", "oct.", "nov.", "déc.",
];

function formatDay(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!match) return null;
  const month = SHORT_MONTHS[Number(match[2]) - 1];
  if (!month) return null;
  return { month, day: Number(match[3]), key: match[2] };
}

function formatStamp(iso, withDay) {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2})Z)?$/.exec(String(iso || ""));
  if (!match) return "";
  const month = SHORT_MONTHS[Number(match[2]) - 1];
  if (!month) return "";
  const day = `${Number(match[3])} ${month}`;
  if (!match[4]) return day;
  const clock = `${match[4]}:${match[5]} UTC`;
  return withDay ? `${day} ${clock}` : clock;
}

function sameUtcDay(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
  if (!match) return false;
  const now = new Date();
  return now.getUTCFullYear() === Number(match[1])
    && now.getUTCMonth() + 1 === Number(match[2])
    && now.getUTCDate() === Number(match[3]);
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = formatInt(value);
}

function formatUtc(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()} à ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
}

function paintCatalog(data) {
  all = Array.isArray(data.fiches) ? data.fiches : [];
  seenAsOf = data.as_of || seenAsOf;
  seenFollowers = (data.followers && data.followers.latest) || seenFollowers;
  setText("total-blocks", data.total_blocks);
  setText("fiche-count", data.fiche_count);
  setText("name-count", data.name_count);
  setText("fiche-sum", data.fiche_count_sum);
  setText("historical", data.historical_base);
  const lede = document.getElementById("hero-lede");
  if (lede) {
    lede.textContent = `${formatInt(data.total_blocks)} blocages déclarés. ${formatInt(data.fiche_count)} fiches. Le débat, lui, est resté dehors.`;
  }
  const caption = document.querySelector(".caption");
  if (caption) {
    const when = formatUtc(data.as_of);
    caption.textContent = when
      ? `Compteur global de la liste, relevé le ${when}. Une partie du total n'a même pas de nom.`
      : "Compteur global de la liste. Une partie du total n'a même pas de nom.";
  }
  const pull = document.getElementById("pull-line");
  if (pull) {
    const when = formatUtc(data.refreshed_at || data.snapshot_at || data.as_of);
    pull.textContent = when
      ? `La liste ci-dessous date du dernier pull, le ${when.replace(" UTC", "\u00A0UTC")}.`
      : "La liste ci-dessous date du dernier pull.";
  }
  paintFollowers(data);
  fillSelect("cat", data.categories || [], "Tous");
  fillSelect("net", data.platforms || [], "Tous");
  $("#net").querySelectorAll("option").forEach((option) => {
    if (!option.value) return;
    const label = PLATFORM_LABEL[option.value] || option.value;
    const count = option.textContent.match(/\(([^)]+)\)$/);
    option.textContent = count ? `${label} (${count[1]})` : label;
  });
}

function paintLive(status) {
  const node = $("#live-line");
  if (!node || !status) return;
  if (status.ok && status.as_of) {
    node.textContent = `En direct. Dernier relevé le ${formatUtc(status.as_of)}. Le bouton, lui, ne dort pas.`;
    return;
  }
  if (status.error) {
    node.textContent = "Le relevé vient de rater. La copie affichée est la dernière qui a marché.";
  }
}



function fillSelect(id, pairs, allLabel) {
  const select = document.getElementById(id);
  const current = select.value;
  select.replaceChildren();
  if (!Array.isArray(pairs)) pairs = [];
  const first = document.createElement("option");
  first.value = "";
  first.textContent = allLabel;
  select.append(first);
  pairs.forEach((pair) => {
    if (!Array.isArray(pair)) return;
    const [label, count] = pair;
    const text = String(label ?? "");
    if (!text) return;
    const option = document.createElement("option");
    option.value = text;
    option.textContent = `${text} (${formatInt(count)})`;
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

function paintFollowers(data) {
  const node = document.getElementById("followers-line");
  if (!node) return;
  const info = data.followers || {};
  const matched = Number(info.matched) || 0;
  const compared = Number(info.compared) || 0;
  if (matched <= 0) {
    node.textContent = "Abonnés : leur followers.js ne retombe sur aucune fiche.";
    return;
  }
  const baseDay = String(info.baseline || "").slice(0, 10);
  const lateDay = String(info.latest || "").slice(0, 10);
  const split = Boolean(baseDay && lateDay && baseDay !== lateDay);
  const from = formatStamp(info.baseline, split);
  const latest = formatStamp(info.latest, split);
  const day = formatDay(baseDay);
  let span = "sur le relevé de base et le dernier passage.";
  if (day && from && latest && !split) {
    const stop = day.month.endsWith(".") ? "" : ".";
    span = `entre ${from} et ${latest}, le ${day.day} ${day.month}${stop}`;
  } else if (from && latest) {
    span = `entre ${from} et ${latest}.`;
  } else if (from) {
    span = `depuis ${from}.`;
  }
  node.textContent = `Abonnés pris dans leur followers.js, relus toutes les heures. ${formatInt(matched)} fiches ont un chiffre. ${formatInt(compared)} ont un écart ${span} Un relevé arrondi au millier reste sans écart.`;
}

function followerLine(fiche) {
  const after = fiche.followers_after;
  const line = document.createElement("p");
  line.className = "fiche-gain";
  if (!Number.isInteger(after) || after <= 0) {
    line.classList.add("is-empty");
    line.textContent = "Abonnés non relevés.";
    return line;
  }
  const num = document.createElement("span");
  num.className = "delta";
  num.textContent = formatInt(after);
  const unit = document.createElement("span");
  unit.textContent = " abonnés";
  line.append(num, unit);
  const cmp = document.createElement("span");
  const gained = fiche.followers_gained;
  if (Number.isInteger(gained)) {
    const since = formatStamp(fiche.followers_from, !sameUtcDay(fiche.followers_from)) || "le relevé précédent";
    if (gained > 0) {
      cmp.className = "cmp up";
      cmp.textContent = `, +${formatInt(gained)} depuis ${since}`;
    } else if (gained < 0) {
      cmp.className = "cmp";
      cmp.textContent = `, ${formatInt(Math.abs(gained))} en moins depuis ${since}`;
    } else {
      cmp.className = "cmp";
      cmp.textContent = `, inchangé depuis ${since}`;
    }
  } else if (fiche.followers_rounded) {
    cmp.className = "cmp";
    cmp.textContent = ", relevé arrondi";
  } else {
    const day = formatDay(fiche.followers_to);
    cmp.className = "cmp";
    cmp.textContent = day ? `, relevé du ${day.day} ${day.month}` : ", relevé public";
  }
  line.append(cmp);
  return line;
}

function renderRow(fiche) {
  const article = document.createElement("article");
  article.className = "fiche";

  const name = document.createElement("h3");
  name.textContent = String(fiche.name ?? "");

  const meta = document.createElement("p");
  meta.className = "fiche-meta";
  const network = PLATFORM_LABEL[fiche.platform] || String(fiche.platform ?? "");
  meta.textContent = [fiche.category, network].filter(Boolean).join(", ");

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
    action.className = "btn fiche-go";
    action.href = href;
    action.target = "_blank";
    action.rel = "noopener noreferrer";
    action.textContent = "Suivre";
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
  reason.textContent = String(fiche.reason || "Motif non inscrit.");

  article.append(name, meta, count, action);
  const gain = followerLine(fiche);
  if (gain) article.append(gain);
  article.append(reason);
  return article;
}

function render() {
  const controls = readControls();
  const rows = filterFiches(all, controls);
  const windowed = pageWindow(rows, page, PAGE_SIZE);
  page = windowed.page;

  $("#status").textContent = rows.length
    ? `${formatInt(rows.length)} fiches au tiroir`
    : "Aucune fiche. Soit le nom est libre, soit l'orthographe a été bloquée avant lui.";
  $("#page-label").textContent = rows.length
    ? `Page ${windowed.page} sur ${windowed.pages}`
    : "Page 0. Le rayon est vide.";
  $("#prev").disabled = !rows.length || windowed.page <= 1;
  $("#next").disabled = !rows.length || windowed.page >= windowed.pages;

  const list = $("#list");
  list.replaceChildren();
  if (!rows.length) {
    const p = document.createElement("p");
    p.className = "state";
    p.textContent = "Rien dans ce rayon. L'orthographe a été bloquée avant le nom.";
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

function paintSubmissions(data) {
  const line = document.getElementById("sub-line");
  const list = document.getElementById("sub-list");
  if (!line || !list || !data || typeof data !== "object") return;
  const when = formatUtc(data.pulled_at).replace(" UTC", "\u00A0UTC");
  list.replaceChildren();
  const section = document.getElementById("guichet");
  if (section) section.classList.toggle("is-shut", !data.ok);
  if (!data.ok) {
    const status = Number(data.http_status) || 0;
    const message = String(data.message || "lecture ratée");
    let text = when
      ? `Pull de /api/submissions, le ${when}. Réponse ${status} : ${message}`
      : `Pull de /api/submissions. Réponse ${status} : ${message}`;
    const published = data.via === "api/accounts" && Array.isArray(data.items) && data.items.length;
    if (published) {
      text += " En attendant la réouverture, les lignes viennent de /api/accounts.";
    }
    line.textContent = text;
    if (!published) return;
  } else {
    const total = Number(data.total) || 0;
    const plural = total > 1 ? "s" : "";
    line.textContent = when
      ? `Pull de /api/submissions, le ${when}. ${formatInt(total)} ligne${plural} publique${plural}.`
      : `Pull de /api/submissions. ${formatInt(total)} ligne${plural} publique${plural}.`;
  }
  const items = Array.isArray(data.items) ? data.items : [];
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "howto";
    empty.textContent = "La réponse ne contient aucune fiche publique. Le guichet a répondu par le vide.";
    list.append(empty);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("article");
    row.className = "sub-row";
    const title = document.createElement("h3");
    title.textContent = String(item.name || "Profil public");
    row.append(title);
    const bits = [SUB_KIND[item.type] || "", PLATFORM_LABEL[item.platform] || ""].filter(Boolean);
    if (item.reason) bits.push(String(item.reason));
    if (bits.length) {
      const meta = document.createElement("p");
      meta.textContent = bits.join(" - ");
      row.append(meta);
    }
    const url = safeProfileUrl(item.url);
    if (url) {
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Profil public";
      row.append(link);
    }
    list.append(row);
  });
}

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text != null) el.textContent = text;
  return el;
}

function plural(count, one, many) {
  return `${formatInt(count)} ${count > 1 ? many : one}`;
}

function pushText(parent, text, marked) {
  if (!text) return;
  if (parent.childNodes.length) parent.append(document.createTextNode(" "));
  if (marked) parent.append(node("span", "mark", text));
  else parent.append(document.createTextNode(text));
}

function stepLine(step, index) {
  const line = document.createElement("p");
  if (step.source === "site") pushText(line, "Relevé du site.");
  else if (index === 0) pushText(line, "Premier relevé.");
  if (step.accounts == null) {
    pushText(line, "Comptes absents.");
  } else if (index === 0 || step.accounts_first) {
    pushText(line, `${formatInt(step.accounts)} lignes.`);
  } else if (step.accounts_same) {
    pushText(line, "Comptes inchangés.");
  } else {
    const marks = [];
    if (step.removed) marks.push(`${plural(step.removed, "retrait", "retraits")}.`);
    if (step.added) marks.push(`${plural(step.added, "ajout", "ajouts")}.`);
    if (step.rewritten) marks.push(`${plural(step.rewritten, "retouche", "retouches")}.`);
    if (marks.length) marks.forEach((text) => pushText(line, text, true));
    else if (step.lines_delta) pushText(line, "Le nombre de lignes change, sans autre retouche.");
    else pushText(line, "Comptes inchangés.");
  }
  if (step.counts_absent) {
    pushText(line, "Compteurs absents.");
  } else if (step.total != null && step.total_delta == null) {
    pushText(line, `Compteur ${formatInt(step.total)}.`);
  } else if (step.total_delta > 0) {
    pushText(line, `Compteur +${formatInt(step.total_delta)}.`);
  } else if (step.total_delta < 0) {
    pushText(line, `Compteur en baisse de ${formatInt(-step.total_delta)}.`, true);
  } else if (step.total_delta === 0) {
    pushText(line, "Compteur stable.");
  }
  if (step.total_delta != null) {
    if (step.down_count) {
      pushText(line, step.down_count > 1
        ? `${formatInt(step.down_count)} compteurs en recul.`
        : "1 compteur en recul.", true);
    } else {
      pushText(line, "Aucun recul.");
    }
  }
  if (step.counts_gap) pushText(line, "Le créneau d'avant n'avait pas de compteur.");
  if (step.keys_added) pushText(line, `${plural(step.keys_added, "clé en plus", "clés en plus")}.`);
  if (step.keys_removed) pushText(line, `${plural(step.keys_removed, "clé en moins", "clés en moins")}.`, true);
  return line;
}

function editRow(item) {
  const row = node("article", "edit");
  const title = node("h3", "", item.name || "Sans nom");
  row.append(title);
  const network = PLATFORM_LABEL[item.platform] || "";
  const meta = [item.category, network].filter(Boolean).join(", ");
  if (meta) row.append(node("p", "before", meta));
  if (item.kind === "removal") {
    row.classList.add("is-removed");
    title.classList.add("mark");
    row.append(node("p", "mark", "Retiré du fichier."));
    if (item.reason) row.append(node("p", "before", `Motif recopié : ${item.reason}`));
  } else if (item.kind === "addition") {
    row.append(node("p", "mark", "Ajouté au fichier."));
    if (item.reason) row.append(node("p", "before", `Motif recopié : ${item.reason}`));
  } else {
    const fields = Array.isArray(item.changes) ? item.changes : [];
    const onlyMotive = fields.length === 1 && fields[0].field === "motif";
    row.append(node("p", "mark", onlyMotive ? "Motif réécrit." : "Fiche réécrite."));
    fields.forEach((change) => {
      if (change.hidden || (!change.before && !change.after)) {
        row.append(node("p", "mark", `${change.field || "Champ"} modifié.`));
        return;
      }
      if (!onlyMotive && change.field) row.append(node("p", "before", change.field));
      if (change.before) row.append(node("p", "before", `Avant : ${change.before}`));
      if (change.after) row.append(node("p", "after", `Après : ${change.after}`));
    });
  }
  const href = safeProfileUrl(item.url);
  if (href) {
    const link = node("a", "", "Profil public");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    row.append(link);
  }
  return row;
}

function paintComparaison(data) {
  const line = document.getElementById("pulls-line");
  const stepsHost = document.getElementById("pulls-steps");
  const editsHost = document.getElementById("pulls-edits");
  const countsHost = document.getElementById("pulls-counts");
  if (!line || !stepsHost || !editsHost || !countsHost || !data || typeof data !== "object") return;
  const bits = ["Les heures du tableau sont celles de Paris."];
  if (data.snapshot_stable && data.snapshot_at) {
    const when = formatUtc(data.snapshot_at);
    if (when) bits.push(`Chaque copie des comptes reste datée du ${when}.`);
  } else if (data.snapshot_stable === false) {
    bits.push("La date inscrite dans le fichier des comptes change d'une copie à l'autre.");
  }
  if (data.frozen === true) bits.push("Le fichier reste marqué gelé.");
  if (data.rejected_stable && Number.isInteger(data.rejected)) {
    bits.push(`${formatInt(data.rejected)} demandes de retrait refusées, inchangées. Ce compte n'est pas une liste de profils qu'on refuserait de bloquer.`);
  }
  if (data.journal && data.journal.lines) {
    let journal = `Le journal du collecteur compte ${formatInt(data.journal.lines)} échecs`;
    if (data.journal.http_404) journal += `, dont ${formatInt(data.journal.http_404)} réponses 404`;
    journal += ". Le texte du journal n'est pas recopié.";
    bits.push(journal);
  }
  if (data.steps_omitted) {
    bits.push(`${formatInt(data.steps_omitted)} copies plus anciennes ne sont pas dans le tableau.`);
  }
  line.textContent = bits.join(" ");

  const steps = Array.isArray(data.steps) ? data.steps : [];
  stepsHost.replaceChildren();
  if (!steps.length) {
    stepsHost.append(node("p", "howto", "Aucune copie à comparer."));
  } else {
    const list = node("ol", "pull-steps");
    steps.forEach((step, index) => {
      const item = node("li", "pull-step");
      const when = step.source === "site" && step.utc
        ? `${step.local} Paris, ${step.utc} UTC`
        : `${step.local || ""} Paris`;
      item.append(node("p", "pull-when", when.trim()));
      item.append(stepLine(step, index));
      list.append(item);
    });
    stepsHost.append(list);
  }

  const edits = Array.isArray(data.edits) ? data.edits : [];
  editsHost.replaceChildren();
  editsHost.append(node("p", "pull-label", edits.length ? "Retouches du fichier" : "Retouches"));
  if (!edits.length) {
    editsHost.append(node("p", "howto", "Aucune retouche de fiche entre ces copies."));
  } else {
    edits.forEach((item) => editsHost.append(editRow(item)));
    if (data.edits_hidden) {
      editsHost.append(node("p", "howto", `Et ${plural(data.edits_hidden, "autre retouche", "autres retouches")}.`));
    }
  }

  const span = data.span || {};
  const gains = Array.isArray(data.gains) ? data.gains : [];
  const downs = Array.isArray(data.downs) ? data.downs : [];
  countsHost.replaceChildren();
  countsHost.append(node("p", "pull-label", "Compteur"));
  if (span.from_total != null && span.to_total != null) {
    let sentence = `Du premier relevé au dernier, le compteur passe de ${formatInt(span.from_total)} à ${formatInt(span.to_total)}`;
    if (span.delta > 0) sentence += `, soit +${formatInt(span.delta)}.`;
    else if (span.delta < 0) sentence += `, soit ${formatInt(-span.delta)} de moins.`;
    else sentence += ", sans écart.";
    if (span.keys_added || span.keys_removed) {
      const parts = [];
      if (span.keys_added) parts.push(plural(span.keys_added, "clé apparaît", "clés apparaissent"));
      if (span.keys_removed) parts.push(plural(span.keys_removed, "clé disparaît", "clés disparaissent"));
      sentence += ` ${parts.join(", ")}.`;
    } else {
      sentence += " Aucune clé n'apparaît ni ne disparaît.";
    }
    sentence += span.net_downs
      ? ` ${plural(span.net_downs, "compteur finit plus bas", "compteurs finissent plus bas")}.`
      : " Aucun compteur ne finit plus bas qu'au début.";
    countsHost.append(node("p", "howto", sentence));
  }
  if (gains.length) {
    const list = node("ol", "pull-gains");
    gains.forEach((item) => {
      const row = document.createElement("li");
      const network = PLATFORM_LABEL[item.platform] || "";
      row.append(node("span", "", [network, item.name].filter(Boolean).join(", ")));
      row.append(node("strong", "", `+${formatInt(item.delta)}`));
      list.append(row);
    });
    countsHost.append(list);
  }
  if (downs.length) {
    countsHost.append(node("p", "pull-label", "Reculs à retenir"));
    const list = node("ol", "pull-gains");
    downs.forEach((item) => {
      const row = document.createElement("li");
      const network = PLATFORM_LABEL[item.platform] || "";
      row.append(node("span", "", [network, item.name, item.local].filter(Boolean).join(", ")));
      row.append(node("strong", "mark", `${formatInt(item.before)} à ${formatInt(item.after)}`));
      list.append(row);
    });
    countsHost.append(list);
    if (data.downs_hidden) {
      countsHost.append(node("p", "howto", `Et ${plural(data.downs_hidden, "autre recul", "autres reculs")}.`));
    }
  }
  if (data.dip_count) {
    const ampleur = data.dip_max <= 1 ? "d'un point" : `de ${formatInt(data.dip_max)} points au plus`;
    countsHost.append(node(
      "p",
      "howto",
      `${plural(data.dip_count, "compteur a baissé", "compteurs ont baissé")} ${ampleur} entre deux relevés, puis ${data.dip_count > 1 ? "sont revenus" : "est revenu"}. Le nom n'est pas repris pour un écart aussi court.`,
    ));
  }
}

async function loadComparaison() {
  const line = document.getElementById("pulls-line");
  try {
    const response = await fetch(`data/comparaison.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    paintComparaison(await response.json());
  } catch {
    if (line) line.textContent = "La comparaison n'est pas arrivée.";
  }
}

async function loadSubmissions() {
  const line = document.getElementById("sub-line");
  try {
    const response = await fetch(`data/submissions.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    paintSubmissions(await response.json());
  } catch {
    if (line) line.textContent = "Pull de /api/submissions raté. Le fichier n'est pas arrivé.";
    document.getElementById("guichet")?.classList.add("is-shut");
  }
}

async function loadFiches() {
  const response = await fetch(`data/fiches.json?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

function paintVisits(data) {
  const node = document.getElementById("visits");
  const note = document.getElementById("visits-note");
  if (!node || !data) return;
  const total = Number(data.total) || 0;
  const today = Number(data.today) || 0;
  node.textContent = formatInt(total);
  if (!note) return;
  if (total <= 0) {
    note.textContent = "Aucun passage pour l'instant. Le registre attend.";
    return;
  }
  if (total === 1) {
    note.textContent = "Un passage. Le registre vient de s'ouvrir.";
    return;
  }
  note.textContent = `Dont ${formatInt(today)} aujourd'hui. Le registre, lui, ne débloque rien.`;
}

function endpoint(name) {
  const here = new URL(document.baseURI);
  if (!here.pathname.endsWith("/")) here.pathname = here.pathname.replace(/[^/]*$/, "");
  return new URL(name, here).href;
}

function markVisitsMissing() {
  const note = document.getElementById("visits-note");
  if (note) {
    note.textContent = "Le registre des passages est sur le serveur. Cette copie ne le tient pas.";
  }
}

async function loadVisits() {
  const node = document.getElementById("visits");
  if (!node) return;
  try {
    const response = await fetch(endpoint("api/visits"), { cache: "no-store" });
    if (!response.ok) {
      if (node.dataset.held !== "1") markVisitsMissing();
      return;
    }
    paintVisits(await response.json());
  } catch {
    if (node.dataset.held !== "1") markVisitsMissing();
  }
}

async function watchLive() {
  try {
    const response = await fetch(endpoint("api/status"), { cache: "no-store" });
    if (response.status === 404) {
      window.clearInterval(liveTimer);
      return;
    }
    if (!response.ok) throw new Error(String(response.status));
    const status = await response.json();
    paintLive(status);
    loadVisits();
    loadSubmissions();
    const blocksMoved = status.ok && status.as_of && status.as_of !== seenAsOf;
    const fansMoved = status.followers_at && status.followers_at !== seenFollowers;
    if (blocksMoved || fansMoved) {
      const data = await loadFiches();
      paintCatalog(data);
      render();
      if (blocksMoved) loadComparaison();
    }
  } catch {
    const node = $("#live-line");
    if (node && seenAsOf) {
      node.textContent = "Le fil direct est coupé. La copie affichée reste la dernière qui a marché.";
    }
  }
}

async function init() {
  const pending = readQuery();
  loadComparaison();
  try {
    const data = await loadFiches();
    paintCatalog(data);
    if (pending.cat && [...$("#cat").options].some((option) => option.value === pending.cat)) {
      $("#cat").value = pending.cat;
    }
    if (pending.net && [...$("#net").options].some((option) => option.value === pending.net)) {
      $("#net").value = pending.net;
    }
    bind();
    render();
    loadVisits();
    loadSubmissions();
    watchLive();
    liveTimer = window.setInterval(watchLive, 15000);
  } catch {
    showState("Le tiroir est coincé. Le fichier des fiches n'est pas arrivé.");
    $("#status").textContent = "Erreur de chargement. Le classeur a avalé la page.";
  }
}

if ($("#list")) init();
