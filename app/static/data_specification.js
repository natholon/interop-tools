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
function crosswalkToCsv(entries) {
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
    // CRLF per RFC 4180.
    return [header, ...rows].map((row) => row.map(csvField).join(",")).join("\r\n");
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

    const sourcePre = document.getElementById("source-highlighted");
    const sourceCode = document.getElementById("source-highlighted-code");
    const editSourceBtn = document.getElementById("edit-source-btn");
    const fhirPlaceholder = document.getElementById("fhir-placeholder");
    const fhirPre = document.getElementById("fhir-highlighted");
    const fhirCode = document.getElementById("fhir-highlighted-code");
    const tooltip = document.getElementById("xwalk-tooltip");

    let currentReportEntries = [];
    let currentReportLabel = "crosswalk";

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

    function showError(category, message) {
        if (!errorPane) return;
        errorPane.innerHTML = "";
        const strong = document.createElement("strong");
        strong.textContent = category;
        const pre = document.createElement("pre");
        pre.textContent = message;
        errorPane.append(strong, pre);
        errorPane.hidden = false;
        if (outputPane) outputPane.hidden = true;
        // Stale entries must not stay downloadable behind a failed run -
        // the same "toggle every pane, not just the obvious one" hazard
        // index.html's own "Use Bundle above" button shipped once.
        currentReportEntries = [];
        if (downloadCsvBtn) downloadCsvBtn.hidden = true;
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


    function renderCrosswalkTable(entries) {
        if (!tableBody) return;
        tableBody.innerHTML = "";
        for (const entry of entries) {
            const tr = document.createElement("tr");
            if (entry.derivation === "inferred") tr.className = "crosswalk-inferred";

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

            tr.append(locationCell, fieldNameCell, pathCell, sourceValueCell, valueCell);
            tableBody.appendChild(tr);
        }
        if (tableWrapper) tableWrapper.hidden = false;
        if (rawJson) rawJson.hidden = true;
    }

    function downloadCrosswalkCsv() {
        if (!currentReportEntries.length) return;
        // Leading BOM so Excel opens it as UTF-8 - without it, Excel
        // guesses the local codepage and mangles any non-ASCII patient
        // name (e.g. "José García"), which real generated samples produce.
        const blob = new Blob(["﻿" + crosswalkToCsv(currentReportEntries)], {
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

    function showCrosswalk(report, highlighting, dedupSummary) {
        if (!outputPane) return;
        currentReportEntries = report.entries || [];
        // e.g. "ADT-A01", "CDA-CCD", "EDI-837P" - a filename that says
        // which message the crosswalk came from, since a user comparing
        // several downloads otherwise gets a folder of identical names.
        currentReportLabel =
            [report.message_type, report.trigger_event]
                .filter(Boolean)
                .join("-")
                .replace(/[^A-Za-z0-9._-]+/g, "_") || "crosswalk";
        if (downloadCsvBtn) downloadCsvBtn.hidden = currentReportEntries.length === 0;

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
        }

        outputPane.hidden = false;
        if (errorPane) errorPane.hidden = true;
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
                showEditableSource();
            };
            reader.readAsText(file);
        });
    }

    if (!form || !textarea) return;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitter = event.submitter;
        setBusy(submitter, true);
        try {
            const response = await fetch("/api/data-specification", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    hl7_text: textarea.value,
                    deduplicate: dedupCheckbox ? dedupCheckbox.checked : false,
                }),
            });
            const data = await response.json();
            if (!response.ok) {
                showError(data.error.category, data.error.message);
                return;
            }
            showCrosswalk(data.report, data.highlighting, data.deduplication);
        } catch (err) {
            showError("Network error", String(err));
        } finally {
            setBusy(submitter, false);
        }
    });
});
