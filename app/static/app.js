// A small, self-contained script for the Data Specification page - kept
// separate from app.js (not sharing its scope, since this app has no
// module system) rather than gated additions inside app.js's own
// DOMContentLoaded listener, since this page's own panes never need to be
// added to index.html's show*() hidden-toggle chains or vice versa - see
// CLAUDE.md's own Data Specification section for why a genuinely separate
// page (not a new pane on index.html) was chosen specifically to sidestep
// that recurring bug class.
//
// escapeHtml/highlightJsonFragment below are the identical escaping/
// token-coloring logic app.js::escapeHtml/highlightJson already use -
// duplicated, not imported (no module system), and highlightJsonFragment
// is deliberately usable on a *partial* JSON fragment (a gap between two
// marks, not necessarily a complete, independently-valid JSON snippet) -
// the regex is token-based, not a real JSON parser, so it degrades
// gracefully (falls through to plain escaped text) at a fragment boundary
// that happens to split a token, rather than misrendering.

const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 };

function debounce(fn, delay) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
    };
}

// Mirrors app/pipeline.py's own format sniff (is_x12 checked first since
// cheapest, then is_xml, else HL7v2 default) - a client-side preview only,
// the server is still the source of truth for what actually gets routed.
function detectFormat(text) {
    const stripped = text.replace(/^\uFEFF/, "").trimStart();
    if (!stripped) return null;
    if (stripped.startsWith("ISA")) return "X12 EDI";
    if (stripped.startsWith("<")) return "C-CDA XML";
    return "HL7v2";
}

function escapeHtml(str) {
    return str.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function highlightJsonFragment(fragment) {
    const escaped = escapeHtml(fragment);
    return escaped.replace(
        /("(\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
        (match) => {
            let cls = "json-number";
            if (/^"/.test(match)) {
                cls = /:$/.test(match) ? "json-key" : "json-string";
            } else if (/^(true|false|null)$/.test(match)) {
                cls = "json-literal";
            }
            return `<span class="${cls}">${match}</span>`;
        }
    );
}

// RFC 4180 field escaping: a field containing a comma, a double quote, or
// any newline must be quoted, and embedded quotes doubled. All three are
// genuinely reachable here - `reason` strings are full sentences with
// commas, and clinical free text (an ORU value, an MDM document body)
// can carry both quotes and newlines.
function csvField(value) {
    const str = value == null ? "" : String(value);
    return /[",\r\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
}

// The crosswalk as CSV. Deliberately *lossless* rather than a pixel-copy
// of the on-screen table: the table folds an inferred entry's `reason`
// into the Source Location column and encodes direct-vs-inferred purely
// as italics, neither of which survives into a data format. So reason and
// derivation each get their own column - an export exists to be analyzed,
// and conflating two different things into one column to mimic a visual
// layout would make it worse at that job.
function crosswalkToCsv(entries, dropped = []) {
    const header = [
        "Source Location",
        "Source Field Name",
        "FHIR Path",
        "Source Value",
        "FHIR Value",
        "Derivation",
        "Reason",
    ];
    const rows = entries.map((entry) => [
        entry.source_location,
        entry.field_label,
        entry.fhir_path,
        // Same fallback the table's own Source Value column uses, so the
        // two can't disagree about what a plain (untransformed) copy shows.
        displayedSourceValue(entry),
        entry.value,
        entry.derivation,
        entry.reason,
    ]);
    // Dropped source data, with the FHIR columns empty - the export shows
    // the same findings the table does, or the two disagree about what the
    // conversion actually did.
    const droppedRows = dropped.map((decision) => [
        decision.source_location,
        decision.field_label,
        "",
        decision.lost_value,
        "",
        "dropped",
        decision.detail,
    ]);
    // CRLF per RFC 4180.
    return [header, ...rows, ...droppedRows].map((row) => row.map(csvField).join(",")).join("\r\n");
}

// source_value is only recorded when a mapper actually transforms the
// field (a date reformatted, a code mapped, ...) - a plain copy never
// sets it, since it would just duplicate `value` verbatim. For a direct
// entry the untransformed source value *is* `value` in that case, so fall
// back to it rather than leaving "Source Value" inconsistently blank
// depending on whether that field happened to need reformatting. Inferred
// entries have no real source field at all, so there's nothing to fall
// back to. Top-level (not scoped to the DOMContentLoaded block) so both
// the table renderer and the CSV export share one definition.
function displayedSourceValue(entry) {
    if (entry.derivation === "inferred") return null;
    return entry.source_value ?? entry.value ?? null;
}

// Builds the HTML for one pane: `text` interleaved with `<mark>`-wrapped
// spans at the given (start, end) offsets. `spans` must be pre-sorted by
// start and non-overlapping (guaranteed by construction - see
// buildSourceSpans/buildFhirSpans below, which only ever draw from
// app/provenance/highlighting.py's own non-overlapping, per-leaf-field
// spans) - a defensively out-of-order/overlapping span is simply skipped
// rather than risking corrupted markup.
function renderWithMarks(text, spans, { syntaxHighlightGaps = false } = {}) {
    let html = "";
    let cursor = 0;
    for (const span of spans) {
        if (span.start < cursor || span.end <= span.start || span.end > text.length) continue;
        const gap = text.slice(cursor, span.start);
        html += syntaxHighlightGaps ? highlightJsonFragment(gap) : escapeHtml(gap);
        const inner = escapeHtml(text.slice(span.start, span.end));
        const classes = ["xwalk-mark"];
        if (span.tokenType) classes.push(`json-${span.tokenType}`);
        const style = span.colorIndex != null ? ` style="--match-color: var(--match-${span.colorIndex})"` : "";
        html += `<mark class="${classes.join(" ")}" data-match="${span.id}"${style}>${inner}</mark>`;
        cursor = span.end;
    }
    html += syntaxHighlightGaps ? highlightJsonFragment(text.slice(cursor)) : escapeHtml(text.slice(cursor));
    return html;
}

function buildSourceSpans(matches) {
    const spans = [];
    matches.forEach((m, i) => {
        if (m.source_span) spans.push({ start: m.source_span[0], end: m.source_span[1], id: i, colorIndex: m.color_index });
    });
    spans.sort((a, b) => a.start - b.start);
    return spans;
}

function buildFhirSpans(matches) {
    const spans = [];
    matches.forEach((m, i) => {
        if (m.fhir_span) {
            spans.push({ start: m.fhir_span[0], end: m.fhir_span[1], id: i, colorIndex: m.color_index, tokenType: m.fhir_token_type });
        }
    });
    spans.sort((a, b) => a.start - b.start);
    return spans;
}

document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("crosswalk-form");
    const textarea = document.getElementById("hl7_text");
    const fileInput = document.getElementById("hl7_file");
    const generateBtn = document.getElementById("crosswalk-generate-sample");
    const messageTypeSelect = document.getElementById("message-type-select");
    const errorPane = document.getElementById("crosswalk-error-pane");
    const outputPane = document.getElementById("crosswalk-output-pane");
    const dedupCheckbox = document.getElementById("crosswalk-deduplicate");
    const dedupSummaryEl = document.getElementById("crosswalk-dedup-summary");
    const unsupportedBanner = document.getElementById("crosswalk-unsupported-banner");
    const tableWrapper = document.getElementById("crosswalk-table-wrapper");
    const tableBody = document.getElementById("crosswalk-table-body");
    const downloadCsvBtn = document.getElementById("download-crosswalk-csv");
    const rawJson = document.getElementById("crosswalk-raw-json");
    const toast = document.getElementById("toast");
    const formatBadge = document.getElementById("format-badge");
    const validationPane = document.getElementById("validation-pane");
    const copyBundleBtn = document.getElementById("copy-bundle");

    const transformForm = document.getElementById("transform-form");
    const transformBundleTextarea = document.getElementById("transform_bundle_json");
    const transformTargetSelect = document.getElementById("transform-target-select");
    const transformErrorPane = document.getElementById("transform-error-pane");
    const transformOutputPane = document.getElementById("transform-output-pane");
    const transformOutputCode = document.getElementById("transform-output-code");
    const copyTransformOutputBtn = document.getElementById("copy-transform-output");
    const useBundleBtn = document.getElementById("use-bundle-for-transform");

    const sourcePre = document.getElementById("source-highlighted");
    const sourceCode = document.getElementById("source-highlighted-code");
    const editSourceBtn = document.getElementById("edit-source-btn");
    const fhirPlaceholder = document.getElementById("fhir-placeholder");
    const fhirPre = document.getElementById("fhir-highlighted");
    const fhirCode = document.getElementById("fhir-highlighted-code");
    const tooltip = document.getElementById("xwalk-tooltip");

    let currentReportEntries = [];
    let currentReportLabel = "crosswalk";
    // The converted Bundle, kept so "Use Bundle above" can hand it straight
    // to the FHIR -> Message form without a second round trip.
    let currentBundleJson = "";
    // Inferred decisions, keyed by the FHIR path they produced, so the
    // crosswalk table can show each conversion beside the decision that
    // produced it instead of making a reader cross-reference two lists.
    let decisionsByPath = new Map();
    let rejectionOutcomeById = new Map();
    // Dropped source data. It has no conversion of its own, so it renders
    // as its own rows in the crosswalk with the FHIR columns blank -
    // source data that went nowhere is still part of the specification.
    let currentDroppedDecisions = [];
    // Review state for the conversion at hand only. Kept in the browser -
    // nothing is stored server-side - and replayed on each request.
    const rejectedDecisionIds = new Set();
    let rerunCrosswalk = () => {};

    const decisionRegister = document.getElementById("decision-register");
    const decisionList = document.getElementById("decision-list");
    const decisionCount = document.getElementById("decision-count");
    const resetDecisionsBtn = document.getElementById("reset-decisions");

    function renderDecisions(decisions, outcomes) {
        decisionsByPath = new Map(
            (decisions || []).filter((d) => d.kind === "inferred" && d.fhir_path).map((d) => [d.fhir_path, d])
        );
        // Both lookups are set before the element guard below: the register
        // block no longer exists, so anything after that guard never runs,
        // and the crosswalk table depends on these.
        currentDroppedDecisions = (decisions || []).filter((d) => d.kind !== "inferred");
        rejectionOutcomeById = new Map((outcomes || []).map((o) => [o.decision_id, o]));
        // The register block was removed once both inferred and dropped
        // decisions moved into the crosswalk table itself.
        if (!decisionRegister || !decisionList) return;
        const outcomeById = new Map((outcomes || []).map((o) => [o.decision_id, o]));
        rejectionOutcomeById = outcomeById;
        decisionList.innerHTML = "";

        if (!decisions.length) {
            decisionRegister.hidden = true;
            return;
        }
        // Inferred decisions now render inline in the Specification
        // Crosswalk, beside the conversion each one produced - listing them
        // here too would duplicate every row and make the page longer, which
        // is the opposite of what showing them inline is for. Dropped
        // decisions have no crosswalk row (nothing was converted), so this
        // is where they live.
        const registerDecisions = [];
        if (!registerDecisions.length) {
            decisionRegister.hidden = true;
            return;
        }
        decisionRegister.hidden = false;
        if (decisionCount) {
            const rejected = registerDecisions.filter((d) => rejectedDecisionIds.has(d.id)).length;
            decisionCount.textContent = rejected
                ? `(${registerDecisions.length} — ${rejected} rejected)`
                : `(${registerDecisions.length})`;
        }
        if (resetDecisionsBtn) resetDecisionsBtn.hidden = rejectedDecisionIds.size === 0;

        for (const decision of registerDecisions) {
            const isRejected = rejectedDecisionIds.has(decision.id);
            const li = document.createElement("li");
            li.className = isRejected ? "decision is-rejected" : "decision";

            const main = document.createElement("div");
            const where = document.createElement("span");
            where.className = "decision-where";
            where.textContent = decision.source_location || decision.fhir_path || "";
            const kind = document.createElement("span");
            kind.className = "decision-kind";
            kind.textContent = decision.kind;
            main.append(where, kind);

            const detail = document.createElement("p");
            detail.className = "decision-detail";
            detail.textContent = decision.detail || decision.summary;
            main.appendChild(detail);

            const cite = document.createElement("p");
            cite.className = decision.citation.authoritative ? "decision-cite" : "decision-cite is-unverified";
            if (decision.citation.url) {
                const link = document.createElement("a");
                link.href = decision.citation.url;
                link.target = "_blank";
                link.rel = "noopener noreferrer";
                link.textContent = decision.citation.title;
                cite.appendChild(link);
            } else {
                cite.textContent = decision.citation.title;
            }
            if (decision.citation.note) {
                cite.appendChild(document.createTextNode(` — ${decision.citation.note}`));
            }
            main.appendChild(cite);

            const actions = document.createElement("div");
            actions.className = "decision-actions";
            const toggle = document.createElement("button");
            toggle.type = "button";
            toggle.className = "btn-panel-action";
            toggle.textContent = isRejected ? "Accept" : "Reject";
            toggle.addEventListener("click", () => {
                if (rejectedDecisionIds.has(decision.id)) rejectedDecisionIds.delete(decision.id);
                else rejectedDecisionIds.add(decision.id);
                rerunCrosswalk();
            });
            actions.appendChild(toggle);

            li.append(main, actions);

            // A rejection that could not be applied must say so - the
            // reviewer needs to know their decision did not take effect.
            const outcome = outcomeById.get(decision.id);
            if (outcome && !outcome.applied) {
                const note = document.createElement("p");
                note.className = "decision-outcome";
                note.textContent = `Not applied: ${outcome.note || "no conformant representation."}`;
                li.appendChild(note);
            }
            decisionList.appendChild(li);
        }
    }

    if (resetDecisionsBtn) {
        resetDecisionsBtn.addEventListener("click", () => {
            rejectedDecisionIds.clear();
            rerunCrosswalk();
        });
    }

    let toastTimer;
    function showToast(message) {
        if (!toast) return;
        toast.textContent = message;
        toast.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => {
            toast.hidden = true;
        }, 2200);
    }

    function setBusy(button, busy) {
        if (!button) return;
        const label = button.querySelector(".btn-label");
        if (busy) {
            if (label) {
                button.dataset.originalLabel = label.textContent;
                label.textContent = button.dataset.busyText || "Working…";
            }
            button.classList.add("is-busy");
            button.disabled = true;
        } else {
            if (label && button.dataset.originalLabel) {
                label.textContent = button.dataset.originalLabel;
            }
            button.classList.remove("is-busy");
            button.disabled = false;
        }
    }

    // Everything that represents "the last successful conversion". Any run
    // that is not itself a successful conversion tears all of it down
    // together - a half-cleared view is how a stale Bundle ends up sitting
    // under a fresh error banner, still reachable via "Use Bundle above".
    // The correlated panes live inside the <form>, above #crosswalk-output-
    // pane, so hiding that pane alone leaves them on screen.
    function resetConversionOutput() {
        currentReportEntries = [];
        currentBundleJson = "";
        if (outputPane) outputPane.hidden = true;
        if (downloadCsvBtn) downloadCsvBtn.hidden = true;
        if (decisionRegister) decisionRegister.hidden = true;
        if (useBundleBtn) useBundleBtn.hidden = true;
        if (copyBundleBtn) copyBundleBtn.hidden = true;
        if (sourceCode) sourceCode.innerHTML = "";
        if (fhirCode) fhirCode.innerHTML = "";
        if (fhirPre) fhirPre.hidden = true;
        if (fhirPlaceholder) fhirPlaceholder.hidden = false;
        hideTooltip();
        showEditableSource();
    }

    function showError(category, message) {
        if (!errorPane) return;
        errorPane.innerHTML = "";
        const strong = document.createElement("strong");
        strong.textContent = category;
        const pre = document.createElement("pre");
        pre.textContent = message;
        errorPane.append(strong, pre);
        errorPane.hidden = false;
        if (validationPane) validationPane.hidden = true;
        resetConversionOutput();
    }

    function updateFormatBadge() {
        if (!formatBadge || !textarea) return;
        const format = detectFormat(textarea.value);
        formatBadge.textContent = format ? `Detected: ${format}` : "";
        formatBadge.hidden = !format;
    }
    updateFormatBadge();
    if (textarea) textarea.addEventListener("input", debounce(updateFormatBadge, 150));

    function buildFindingsSummary(findings) {
        const counts = { error: 0, warning: 0, info: 0 };
        for (const f of findings) {
            if (counts[f.severity] !== undefined) counts[f.severity]++;
        }
        const summary = document.createElement("div");
        summary.className = "finding-summary";
        const parts = [];
        if (counts.error) parts.push(`<span class="count-error">${counts.error} error${counts.error === 1 ? "" : "s"}</span>`);
        if (counts.warning) parts.push(`<span class="count-warning">${counts.warning} warning${counts.warning === 1 ? "" : "s"}</span>`);
        if (counts.info) parts.push(`<span class="count-info">${counts.info} info</span>`);
        summary.innerHTML = parts.join(" · ");
        return { summary, counts };
    }

    function buildSeverityFilters(counts, list) {
        const wrap = document.createElement("div");
        wrap.className = "severity-filters";
        const options = [
            ["all", "All"],
            ["error", "Error"],
            ["warning", "Warning"],
            ["info", "Info"],
        ];
        for (const [key, labelText] of options) {
            if (key !== "all" && !counts[key]) continue;
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "filter-chip";
            btn.textContent = labelText;
            btn.setAttribute("aria-pressed", key === "all" ? "true" : "false");
            btn.addEventListener("click", () => {
                wrap.querySelectorAll(".filter-chip").forEach((b) => b.setAttribute("aria-pressed", "false"));
                btn.setAttribute("aria-pressed", "true");
                for (const li of list.children) {
                    li.hidden = key !== "all" && li.dataset.severity !== key;
                }
            });
            wrap.appendChild(btn);
        }
        return wrap;
    }

    function showValidationResult(report) {
        if (!validationPane) return;
        validationPane.innerHTML = "";
        const heading = document.createElement("h2");
        heading.textContent = report.is_valid
            ? "Validation Report — no errors found"
            : "Validation Report — issues found";
        validationPane.appendChild(heading);

        if (!report.findings.length) {
            const p = document.createElement("p");
            p.textContent = "No issues found.";
            validationPane.appendChild(p);
        } else {
            const sorted = [...report.findings].sort(
                (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3)
            );

            const list = document.createElement("ul");
            list.className = "findings-list";
            for (const finding of sorted) {
                const li = document.createElement("li");
                li.className = `finding finding-${finding.severity}`;
                li.dataset.severity = finding.severity;

                const severitySpan = document.createElement("span");
                severitySpan.className = "finding-severity";
                severitySpan.textContent = finding.severity.toUpperCase();

                const locationSpan = document.createElement("span");
                locationSpan.className = "finding-location";
                locationSpan.textContent = finding.segment
                    ? finding.field
                        ? `${finding.segment}-${finding.field}`
                        : finding.segment
                    : "(message)";

                const messageSpan = document.createElement("span");
                messageSpan.className = "finding-message";
                messageSpan.textContent = finding.message;

                li.append(severitySpan, locationSpan, messageSpan);
                list.appendChild(li);
            }

            const { summary, counts } = buildFindingsSummary(report.findings);
            validationPane.appendChild(summary);
            validationPane.appendChild(buildSeverityFilters(counts, list));
            validationPane.appendChild(list);
        }

        // Validate runs against the source message, not a Bundle, and the
        // source is editable again afterwards - so a previous conversion's
        // output is torn down rather than left half-visible beside it.
        validationPane.hidden = false;
        if (errorPane) errorPane.hidden = true;
        resetConversionOutput();
    }

    function showEditableSource() {
        if (textarea) textarea.hidden = false;
        if (sourcePre) sourcePre.hidden = true;
        if (editSourceBtn) editSourceBtn.hidden = true;
    }

    function showHighlightedSource() {
        if (textarea) textarea.hidden = true;
        if (sourcePre) sourcePre.hidden = false;
        if (editSourceBtn) editSourceBtn.hidden = false;
    }

    if (editSourceBtn) {
        editSourceBtn.addEventListener("click", () => {
            showEditableSource();
            if (textarea) textarea.focus();
        });
    }


    function citationNode(citation) {
    const cite = document.createElement("span");
    cite.className = citation.authoritative ? "decision-cite" : "decision-cite is-unverified";
    if (citation.url) {
        const link = document.createElement("a");
        link.href = citation.url;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = citation.title;
        cite.appendChild(link);
    } else {
        cite.textContent = citation.title;
    }
    // The note is a full sentence - the longest runs past 300 characters -
    // and inlining it made a quarter of the crosswalk rows 3-6x taller than
    // the rest and pushed the table to nearly 2500px inside an 870px pane.
    // It rides as a tooltip instead: the reason stays one hover away
    // without competing with the title for the column.
    if (citation.note) {
        cite.title = citation.note;
        cite.classList.add("has-note");
    }
    return cite;
}

function renderCrosswalkTable(entries) {
        if (!tableBody) return;
        tableBody.innerHTML = "";
        // The row index IS the match id: highlighting.matches was built in
        // this same entry order, which is what data-match already encodes
        // on every <mark>. Carrying it here makes a row a second way to
        // locate the same fact, and the keyboard-reachable one.
        for (const [matchIndex, entry] of entries.entries()) {
            const tr = document.createElement("tr");
            tr.dataset.match = String(matchIndex);
            tr.classList.add("crosswalk-locatable");
            // Focusable so the panes' marks are reachable without a mouse.
            // The row carries it rather than each <mark>: a document has
            // hundreds of marks, and hundreds of tab stops would wreck
            // navigation for everything else on the page.
            tr.tabIndex = 0;
            if (entry.derivation === "inferred") tr.classList.add("crosswalk-inferred");

            const locationCell = document.createElement("td");
            if (entry.derivation === "inferred") {
                const em = document.createElement("em");
                em.textContent = entry.reason || "(inferred)";
                locationCell.appendChild(em);
            } else {
                locationCell.textContent = entry.source_location || "";
            }

            const fieldNameCell = document.createElement("td");
            fieldNameCell.textContent = entry.field_label || "";

            const pathCell = document.createElement("td");
            pathCell.textContent = entry.fhir_path;

            const sourceValueCell = document.createElement("td");
            sourceValueCell.textContent = displayedSourceValue(entry) ?? "";

            const valueCell = document.createElement("td");
            valueCell.textContent = entry.value ?? "";

            const decisionCell = document.createElement("td");
            decisionCell.className = "crosswalk-decision";
            const decision = decisionsByPath.get(entry.fhir_path);
            const badge = document.createElement("span");
            if (decision) {
                const isRejected = rejectedDecisionIds.has(decision.id);
                if (isRejected) tr.classList.add("is-rejected");
                badge.className = "decision-badge decision-badge-inferred";
                badge.textContent = isRejected ? "Inferred — rejected" : "Inferred";
                decisionCell.appendChild(badge);
                if (decision.citation && decision.citation.title) {
                    const cite = document.createElement("span");
                    cite.className = decision.citation.authoritative
                        ? "decision-cite"
                        : "decision-cite is-unverified";
                    cite.textContent = decision.citation.title;
                    decisionCell.appendChild(cite);
                }
                // The reject control moved here with the decision, or
                // rejecting an inferred value would no longer be reachable.
                const toggle = document.createElement("button");
                toggle.type = "button";
                toggle.className = "btn-panel-action decision-inline-toggle";
                toggle.textContent = isRejected ? "Accept" : "Reject";
                toggle.addEventListener("click", () => {
                    if (rejectedDecisionIds.has(decision.id)) rejectedDecisionIds.delete(decision.id);
                    else rejectedDecisionIds.add(decision.id);
                    rerunCrosswalk();
                });
                decisionCell.appendChild(toggle);
                const outcome = rejectionOutcomeById.get(decision.id);
                if (outcome && !outcome.applied) {
                    const note = document.createElement("span");
                    note.className = "decision-outcome";
                    note.textContent = `Not applied: ${outcome.note || "no conformant representation."}`;
                    decisionCell.appendChild(note);
                }
            } else if (entry.derivation === "direct") {
                const transformed =
                    entry.source_value != null && entry.value != null && entry.source_value !== entry.value;
                badge.className = transformed
                    ? "decision-badge decision-badge-transformed"
                    : "decision-badge decision-badge-direct";
                badge.textContent = transformed ? "Transformed" : "Direct copy";
                decisionCell.appendChild(badge);
                // A transformed value was governed by something - a
                // ConceptMap, a code table, a datatype's own format rules,
                // or a disclosed local decision. Cite it, so the row says
                // why the value changed rather than only that it did.
                // Shown whenever there is one: a value copied unchanged can
                // still carry a caveat (Composition.confidentiality is
                // correct per the base mapping and prohibited by US Realm
                // Header), and that belongs in the Mapping Decision column.
                if (entry.transform_citation) {
                    decisionCell.appendChild(citationNode(entry.transform_citation));
                }
            }

            tr.append(locationCell, fieldNameCell, pathCell, sourceValueCell, valueCell, decisionCell);
            tableBody.appendChild(tr);
        }

        // Dropped source data, as rows of the same table. The FHIR columns
        // stay empty because nothing was produced - that emptiness is the
        // finding, and it reads better beside the conversions than in a
        // separate block above them.
        for (const decision of currentDroppedDecisions) {
            const tr = document.createElement("tr");
            tr.className = "crosswalk-dropped";

            const cells = [
                decision.source_location || "",
                decision.field_label || "",
                "",
                decision.lost_value || "",
                "",
            ].map((text) => {
                const td = document.createElement("td");
                td.textContent = text;
                return td;
            });

            const decisionCell = document.createElement("td");
            decisionCell.className = "crosswalk-decision";
            const badge = document.createElement("span");
            badge.className = "decision-badge decision-badge-dropped";
            badge.textContent = "Dropped";
            decisionCell.appendChild(badge);
            const cite = document.createElement("span");
            cite.className = decision.citation.authoritative
                ? "decision-cite"
                : "decision-cite is-unverified";
            cite.textContent = decision.citation.title;
            decisionCell.appendChild(cite);
            if (decision.detail) {
                const detail = document.createElement("span");
                detail.className = "decision-outcome";
                detail.textContent = decision.detail;
                decisionCell.appendChild(detail);
            }

            tr.append(...cells, decisionCell);
            tableBody.appendChild(tr);
        }
        if (tableWrapper) tableWrapper.hidden = false;
        if (rawJson) rawJson.hidden = true;
    }

    function downloadCrosswalkCsv() {
        if (!currentReportEntries.length && !currentDroppedDecisions.length) return;
        // Leading BOM so Excel opens it as UTF-8 - without it, Excel
        // guesses the local codepage and mangles any non-ASCII patient
        // name (e.g. "José García"), which real generated samples produce.
        const blob = new Blob(["﻿" + crosswalkToCsv(currentReportEntries, currentDroppedDecisions)], {
            type: "text/csv;charset=utf-8;",
        });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${currentReportLabel}-crosswalk.csv`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showToast("Crosswalk downloaded.");
    }

    if (downloadCsvBtn) downloadCsvBtn.addEventListener("click", downloadCrosswalkCsv);

    // --- caret position readout -------------------------------------
    // Clicking anywhere in either pane says what is under the caret:
    // "PID-5.1" in the source, "Bundle.entry[1].resource.name" in the
    // FHIR JSON. The index comes from the server (see
    // app/provenance/position_index.py) because resolving an offset to a
    // location is a parsing problem, and parsing lives in Python here.
    const sourceCaret = document.getElementById("source-caret");
    const fhirCaret = document.getElementById("fhir-caret");
    let sourcePositions = [];
    let fhirPositions = [];
    // The index describes one exact string. Editing the source invalidates
    // it, so the readout goes quiet rather than reporting stale locations.
    let sourceIndexText = null;

    function setPositions(highlighting) {
        sourcePositions = (highlighting && highlighting.source_positions) || [];
        fhirPositions = (highlighting && highlighting.fhir_positions) || [];
        sourceIndexText = highlighting ? highlighting.display_source_text : null;
        clearCaret(sourceCaret);
        clearCaret(fhirCaret);
    }

    function clearCaret(el) {
        if (el) el.textContent = "";
    }

    // The most specific span containing the offset: entries can nest
    // (a component inside a field inside a segment), and the innermost is
    // what the reader is actually pointing at.
    function locationAt(positions, offset) {
        let best = null;
        for (const entry of positions) {
            if (offset < entry.start || offset >= entry.end) continue;
            if (!best || entry.end - entry.start < best.end - best.start) best = entry;
        }
        return best ? best.path : null;
    }

    function renderCaret(el, label, path) {
        if (!el) return;
        el.textContent = "";
        if (!path) return;
        const labelEl = document.createElement("span");
        labelEl.className = "caret-readout-label";
        labelEl.textContent = label;
        const pathEl = document.createElement("span");
        pathEl.className = "caret-readout-path";
        pathEl.textContent = path;
        el.append(labelEl, pathEl);
    }

    // Offset of the caret inside a <pre>, which unlike a textarea has no
    // selectionStart: measure a range from the element's start to the
    // caret and take its text length. The rendered text matches the
    // indexed string exactly (the HL7v2 \r -> \n substitution is
    // one-for-one), so lengths line up.
    function preCaretOffset(pre) {
        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0) return null;
        const range = selection.getRangeAt(0);
        if (!pre.contains(range.startContainer)) return null;
        const measured = range.cloneRange();
        measured.selectNodeContents(pre);
        measured.setEnd(range.startContainer, range.startOffset);
        return measured.toString().length;
    }

    function watchTextarea(el, positionsFor, caretEl, label) {
        if (!el) return;
        const update = () => {
            if (sourceIndexText !== null && el.value !== sourceIndexText) {
                clearCaret(caretEl);
                return;
            }
            renderCaret(caretEl, label, locationAt(positionsFor(), el.selectionStart));
        };
        ["click", "keyup", "select"].forEach((evt) => el.addEventListener(evt, update));
        el.addEventListener("input", () => clearCaret(caretEl));
    }

    function watchPre(el, positionsFor, caretEl, label) {
        if (!el) return;
        const update = () => {
            const offset = preCaretOffset(el);
            renderCaret(caretEl, label, offset === null ? null : locationAt(positionsFor(), offset));
        };
        ["click", "keyup"].forEach((evt) => el.addEventListener(evt, update));
    }

    watchTextarea(textarea, () => sourcePositions, sourceCaret, "Source location:");
    watchPre(sourcePre, () => sourcePositions, sourceCaret, "Source location:");
    watchPre(fhirPre, () => fhirPositions, fhirCaret, "FHIR path:");

    function showCrosswalk(report, highlighting, dedupSummary) {
        if (!outputPane) return;
        // A pin refers to marks about to be replaced wholesale.
        clearPin();
        setPositions(highlighting);
        currentReportEntries = report.entries || [];
        // e.g. "ADT-A01", "CDA-CCD", "EDI-837P" - a filename that says
        // which message the crosswalk came from, since a user comparing
        // several downloads otherwise gets a folder of identical names.
        currentReportLabel =
            [report.message_type, report.trigger_event]
                .filter(Boolean)
                .join("-")
                .replace(/[^A-Za-z0-9._-]+/g, "_") || "crosswalk";
        if (downloadCsvBtn) downloadCsvBtn.hidden = currentReportEntries.length === 0 && currentDroppedDecisions.length === 0;

        if (dedupSummaryEl) {
            if (dedupSummary) {
                const count = dedupSummary.resources_merged || 0;
                dedupSummaryEl.textContent = count
                    ? `Merged ${count} duplicate resource${count === 1 ? "" : "s"}.`
                    : "No duplicate resources found.";
                dedupSummaryEl.hidden = false;
            } else {
                dedupSummaryEl.hidden = true;
            }
        }

        if (unsupportedBanner) {
            if (report.unsupported) {
                unsupportedBanner.textContent = report.unsupported_reason || "Field-level provenance isn't implemented yet for this input.";
                unsupportedBanner.hidden = false;
            } else {
                unsupportedBanner.hidden = true;
            }
        }

        renderCrosswalkTable(currentReportEntries);

        if (highlighting && sourceCode && fhirCode) {
            const sourceSpans = buildSourceSpans(highlighting.matches || []);
            const fhirSpans = buildFhirSpans(highlighting.matches || []);
            sourceCode.innerHTML = renderWithMarks(highlighting.display_source_text, sourceSpans);
            fhirCode.innerHTML = renderWithMarks(highlighting.fhir_json_text, fhirSpans, { syntaxHighlightGaps: true });
            showHighlightedSource();
            if (fhirPlaceholder) fhirPlaceholder.hidden = true;
            if (fhirPre) fhirPre.hidden = false;
            currentBundleJson = highlighting.fhir_json_text || "";
        }
        // Toggled here rather than only server-rendered - the JS path never
        // re-renders the template, so a button left hidden at page load stays
        // hidden forever otherwise (the exact bug this button shipped with once).
        if (useBundleBtn) useBundleBtn.hidden = !currentBundleJson;
        if (copyBundleBtn) copyBundleBtn.hidden = !currentBundleJson;

        outputPane.hidden = false;
        if (errorPane) errorPane.hidden = true;
        if (validationPane) validationPane.hidden = true;
    }

    // ---- hover correlation: mark <-> mark across both panes, plus a
    // floating tooltip with the fact's own detail (pulled from the
    // already-fetched report.entries, matched by array position - the
    // same order highlighting.matches was built in). ----

    function findEntryForMatchId(matchId) {
        return currentReportEntries[Number(matchId)];
    }

    function setActive(matchId, active) {
        document.querySelectorAll(`.xwalk-mark[data-match="${matchId}"]`).forEach((el) => {
            el.classList.toggle("is-active", active);
        });
    }

    function positionTooltip(x, y) {
        if (!tooltip) return;
        const margin = 12;
        const rect = tooltip.getBoundingClientRect();
        let left = x + margin;
        let top = y + margin;
        if (left + rect.width > window.innerWidth) left = x - rect.width - margin;
        if (top + rect.height > window.innerHeight) top = y - rect.height - margin;
        tooltip.style.left = `${Math.max(4, left)}px`;
        tooltip.style.top = `${Math.max(4, top)}px`;
    }

    function showTooltip(matchId, x, y) {
        if (!tooltip) return;
        const entry = findEntryForMatchId(matchId);
        if (!entry) return;
        const rows = [];
        if (entry.derivation === "inferred") {
            rows.push(["Inferred", entry.reason || "(no source field)"]);
        } else {
            rows.push(["Source Location", entry.source_location || ""]);
            if (entry.field_label) rows.push(["Source Field Name", entry.field_label]);
        }
        const sourceValue = displayedSourceValue(entry);
        if (sourceValue != null) rows.push(["Source Value", sourceValue]);
        rows.push(["FHIR Path", entry.fhir_path]);
        if (entry.value != null) rows.push(["FHIR Value", entry.value]);

        const dl = document.createElement("dl");
        dl.style.margin = "0";
        for (const [label, value] of rows) {
            const dt = document.createElement("dt");
            dt.textContent = label;
            const dd = document.createElement("dd");
            dd.textContent = value;
            dl.append(dt, dd);
        }
        tooltip.innerHTML = "";
        tooltip.appendChild(dl);
        tooltip.hidden = false;
        positionTooltip(x, y);
    }

    function hideTooltip() {
        if (tooltip) tooltip.hidden = true;
    }

    function handlePaneMouseOver(event) {
        const mark = event.target.closest(".xwalk-mark");
        if (!mark) return;
        const matchId = mark.dataset.match;
        setActive(matchId, true);
        showTooltip(matchId, event.clientX, event.clientY);
    }

    function handlePaneMouseOut(event) {
        const mark = event.target.closest(".xwalk-mark");
        if (!mark) return;
        const related = event.relatedTarget;
        if (related && mark.contains(related)) return;
        setActive(mark.dataset.match, false);
        hideTooltip();
    }

    function handlePaneMouseMove(event) {
        if (!tooltip || tooltip.hidden) return;
        positionTooltip(event.clientX, event.clientY);
    }

    for (const pane of [sourcePre, fhirPre]) {
        if (!pane) continue;
        pane.addEventListener("mouseover", handlePaneMouseOver);
        pane.addEventListener("mouseout", handlePaneMouseOut);
        pane.addEventListener("mousemove", handlePaneMouseMove);
    }

    // ---- click to locate: pin a match and bring its counterpart into
    // view in the other pane. Hover already correlates the two, but only
    // for spans that happen to be on screen together, and only while the
    // pointer stays put - clicking "Linda" in a given-name field should
    // scroll the Bundle to the name it produced and keep it marked. ----

    let pinnedMatchId = null;

    function marksFor(matchId) {
        return document.querySelectorAll(`.xwalk-mark[data-match="${matchId}"]`);
    }

    // Scrolls the pane, not the page: scrollIntoView would also move the
    // document, dragging the element the reader just clicked in the other
    // pane out from under the pointer.
    function scrollWithinPane(pane, mark) {
        const paneRect = pane.getBoundingClientRect();
        const markRect = mark.getBoundingClientRect();
        if (markRect.top >= paneRect.top && markRect.bottom <= paneRect.bottom) return;
        const centred = markRect.top - paneRect.top - (pane.clientHeight - markRect.height) / 2;
        pane.scrollTo({ top: pane.scrollTop + centred, behavior: "smooth" });
    }

    function clearPin() {
        if (pinnedMatchId === null) return;
        marksFor(pinnedMatchId).forEach((el) => el.classList.remove("is-pinned"));
        if (tableBody) {
            tableBody.querySelectorAll("tr.is-pinned").forEach((tr) => tr.classList.remove("is-pinned"));
        }
        pinnedMatchId = null;
    }

    // `origin` is the pane the click came from, if any - it keeps its
    // scroll position, since the reader is already looking at it.
    function pinMatch(matchId, origin) {
        const alreadyPinned = pinnedMatchId === matchId;
        clearPin();
        if (alreadyPinned) return;

        pinnedMatchId = matchId;
        marksFor(matchId).forEach((el) => el.classList.add("is-pinned"));
        // The table row is marked but deliberately not scrolled to: it
        // sits below the panes, so reaching it means moving the page, and
        // the reader's eye is on the two panes.
        const row = tableBody && tableBody.querySelector(`tr[data-match="${matchId}"]`);
        if (row) row.classList.add("is-pinned");

        const entry = findEntryForMatchId(matchId);
        const readouts = [
            [sourcePre, sourceCaret, "Source location:", entry && entry.source_location],
            [fhirPre, fhirCaret, "FHIR path:", entry && entry.fhir_path],
        ];
        for (const [pane, caretEl, label, path] of readouts) {
            // The path is reported whether or not a span was highlightable
            // - a fact with no drawable counterpart still has an address
            // worth naming, and saying nothing reads as a dead click.
            renderCaret(caretEl, label, path || null);
            if (!pane || pane === origin) continue;
            const mark = pane.querySelector(`.xwalk-mark[data-match="${matchId}"]`);
            if (mark) scrollWithinPane(pane, mark);
        }
    }

    function handlePaneClick(event) {
        const mark = event.target.closest(".xwalk-mark");
        if (!mark) {
            clearPin();
            return;
        }
        pinMatch(mark.dataset.match, event.currentTarget);
    }

    for (const pane of [sourcePre, fhirPre]) {
        if (pane) pane.addEventListener("click", handlePaneClick);
    }

    if (tableBody) {
        tableBody.addEventListener("click", (event) => {
            // The Reject/Accept control lives in a row too, and re-runs the
            // conversion - it must not double as a locate.
            if (event.target.closest("button")) return;
            const row = event.target.closest("tr[data-match]");
            if (row) pinMatch(row.dataset.match, null);
        });
        tableBody.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            if (event.target.closest("button")) return;
            const row = event.target.closest("tr[data-match]");
            if (!row) return;
            event.preventDefault(); // Space would otherwise page down.
            pinMatch(row.dataset.match, null);
        });
    }

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") clearPin();
    });

    if (generateBtn && messageTypeSelect && textarea) {
        generateBtn.addEventListener("click", async () => {
            const [messageType, triggerEvent] = messageTypeSelect.value.split("|");
            setBusy(generateBtn, true);
            try {
                const response = await fetch(
                    `/api/generate?message_type=${encodeURIComponent(messageType)}&trigger_event=${encodeURIComponent(triggerEvent)}`
                );
                const data = await response.json();
                if (!response.ok) {
                    showError(data.error.category, data.error.message);
                    return;
                }
                textarea.value = data.hl7_text;
                // Setting .value programmatically fires no "input" event, so
                // the badge has to be refreshed by hand here and below.
                updateFormatBadge();
                showEditableSource();
            } catch (err) {
                showError("Network error", String(err));
            } finally {
                setBusy(generateBtn, false);
            }
        });
    }

    if (fileInput && textarea) {
        fileInput.addEventListener("change", () => {
            const file = fileInput.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = () => {
                textarea.value = reader.result;
                fileInput.value = "";
                updateFormatBadge();
                showEditableSource();
            };
            reader.readAsText(file);
        });
    }

    // ---- FHIR -> Message ----

    function showTransformError(category, message) {
        if (!transformErrorPane) return;
        transformErrorPane.innerHTML = "";
        const strong = document.createElement("strong");
        strong.textContent = category;
        const pre = document.createElement("pre");
        pre.textContent = message;
        transformErrorPane.append(strong, pre);
        transformErrorPane.hidden = false;
        if (transformOutputPane) transformOutputPane.hidden = true;
    }

    function showTransformResult(messageText) {
        if (!transformOutputCode || !transformOutputPane) return;
        // HL7v2's bare \r segment terminator doesn't reliably render as a
        // line break inside <pre>/<code> the way it does in a <textarea> -
        // display-only substitution (the copy button below copies this
        // same \n-terminated text, which app/hl7/parser.py::parse_message
        // already normalizes back to \r on the way in, so round-tripping
        // through this app's own forms is unaffected).
        transformOutputCode.textContent = messageText.replace(/\r\n?/g, "\n");
        transformOutputPane.hidden = false;
        if (transformErrorPane) transformErrorPane.hidden = true;
    }

    if (useBundleBtn && transformBundleTextarea) {
        useBundleBtn.addEventListener("click", () => {
            transformBundleTextarea.value = currentBundleJson;
            transformBundleTextarea.scrollIntoView({ behavior: "smooth", block: "center" });
        });
    }

    if (copyBundleBtn) {
        copyBundleBtn.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(currentBundleJson);
                showToast("Copied Bundle JSON to clipboard");
            } catch (err) {
                showToast("Copy failed - select and copy manually");
            }
        });
    }

    if (copyTransformOutputBtn && transformOutputCode) {
        copyTransformOutputBtn.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(transformOutputCode.textContent);
                showToast("Copied generated message to clipboard");
            } catch (err) {
                showToast("Copy failed - select and copy manually");
            }
        });
    }

    if (transformForm && transformBundleTextarea && transformTargetSelect) {
        transformForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            const submitter = event.submitter;
            const [targetFormat, rest] = transformTargetSelect.value.split(" ");
            const [targetType, targetTrigger] = (rest || "").split("^");
            setBusy(submitter, true);
            try {
                const response = await fetch("/api/transform", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        bundle_json: transformBundleTextarea.value,
                        target_format: targetFormat || "",
                        target_type: targetType || "",
                        target_trigger: targetTrigger || "",
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    showTransformError(data.error.category, data.error.message);
                    return;
                }
                showTransformResult(data.message_text);
            } catch (err) {
                showTransformError("Network error", String(err));
            } finally {
                setBusy(submitter, false);
            }
        });
    }

    if (!form || !textarea) return;

    async function runValidation(submitter) {
        setBusy(submitter, true);
        try {
            const response = await fetch("/api/validate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ hl7_text: textarea.value }),
            });
            const data = await response.json();
            if (!response.ok) {
                showError(data.error.category, data.error.message);
                return;
            }
            showValidationResult(data.report);
        } catch (err) {
            showError("Network error", String(err));
        } finally {
            setBusy(submitter, false);
        }
    }

    async function runCrosswalk(submitter) {
        setBusy(submitter, true);
        try {
            const response = await fetch("/api/data-specification", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    hl7_text: textarea.value,
                    deduplicate: dedupCheckbox ? dedupCheckbox.checked : false,
                    // The review is replayed on every request - the server
                    // stores nothing, so rejections only ever apply to the
                    // conversion at hand.
                    rejected_decision_ids: [...rejectedDecisionIds],
                }),
            });
            const data = await response.json();
            if (!response.ok) {
                showError(data.error.category, data.error.message);
                return;
            }
            // Decisions first: the crosswalk table shows each conversion
            // beside the decision that produced it, so the lookup has to be
            // populated before the table renders or every inferred row
            // draws a blank decision cell.
            renderDecisions(data.decisions || [], data.rejection_outcomes || []);
            showCrosswalk(data.report, data.highlighting, data.deduplication);
        } catch (err) {
            showError("Network error", String(err));
        } finally {
            setBusy(submitter, false);
        }
    }

    rerunCrosswalk = () => runCrosswalk(null);

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const submitter = event.submitter;
        // Both buttons submit the one source form; formaction is what tells
        // them apart, matching the no-JS fallback's own routing exactly.
        const isValidate = Boolean(submitter && submitter.formAction && submitter.formAction.endsWith("/validate"));
        if (isValidate) {
            runValidation(submitter);
        } else {
            runCrosswalk(submitter);
        }
    });
});
