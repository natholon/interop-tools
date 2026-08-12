const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 };

document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("convert-form");
    const textarea = document.getElementById("hl7_text");
    const fileInput = document.getElementById("hl7_file");
    const generateBtn = document.getElementById("generate-sample");
    const messageTypeSelect = document.getElementById("message-type-select");
    const outputPane = document.getElementById("output-pane");
    const outputCode = document.getElementById("output-code");
    const errorPane = document.getElementById("error-pane");
    const validationPane = document.getElementById("validation-pane");

    function showError(category, message) {
        errorPane.innerHTML = "";
        const strong = document.createElement("strong");
        strong.textContent = category;
        const pre = document.createElement("pre");
        pre.textContent = message;
        errorPane.append(strong, pre);
        errorPane.hidden = false;
        outputPane.hidden = true;
        validationPane.hidden = true;
    }

    if (generateBtn && messageTypeSelect) {
        generateBtn.addEventListener("click", async () => {
            const [messageType, triggerEvent] = messageTypeSelect.value.split("|");
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
            } catch (err) {
                showError("Network error", String(err));
            }
        });
    }

    if (fileInput) {
        fileInput.addEventListener("change", () => {
            const file = fileInput.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = () => {
                textarea.value = reader.result;
                fileInput.value = "";
            };
            reader.readAsText(file);
        });
    }

    function showResult(bundle) {
        outputCode.textContent = JSON.stringify(bundle, null, 2);
        outputPane.hidden = false;
        errorPane.hidden = true;
        validationPane.hidden = true;
    }

    function showValidationResult(report) {
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
            const list = document.createElement("ul");
            list.className = "findings-list";
            const sorted = [...report.findings].sort(
                (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3)
            );
            for (const finding of sorted) {
                const li = document.createElement("li");
                li.className = `finding finding-${finding.severity}`;

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
            validationPane.appendChild(list);
        }

        validationPane.hidden = false;
        outputPane.hidden = true;
        errorPane.hidden = true;
    }

    if (!form) return;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const isValidate = Boolean(
            event.submitter && event.submitter.formAction && event.submitter.formAction.endsWith("/validate")
        );
        const endpoint = isValidate ? "/api/validate" : "/api/convert";
        try {
            const response = await fetch(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ hl7_text: textarea.value }),
            });
            const data = await response.json();
            if (!response.ok) {
                showError(data.error.category, data.error.message);
                return;
            }
            if (isValidate) {
                showValidationResult(data.report);
            } else {
                showResult(data.bundle);
            }
        } catch (err) {
            showError("Network error", String(err));
        }
    });
});
