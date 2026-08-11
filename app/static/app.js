document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("convert-form");
    const textarea = document.getElementById("hl7_text");
    const fileInput = document.getElementById("hl7_file");
    const sampleBtn = document.getElementById("load-sample");
    const outputPane = document.getElementById("output-pane");
    const outputCode = document.getElementById("output-code");
    const errorPane = document.getElementById("error-pane");
    const sampleData = document.getElementById("sample-message-data");

    if (sampleBtn && sampleData) {
        sampleBtn.addEventListener("click", () => {
            textarea.value = JSON.parse(sampleData.textContent);
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

    function showError(category, message) {
        errorPane.innerHTML = "";
        const strong = document.createElement("strong");
        strong.textContent = category;
        const pre = document.createElement("pre");
        pre.textContent = message;
        errorPane.append(strong, pre);
        errorPane.hidden = false;
        outputPane.hidden = true;
    }

    function showResult(bundle) {
        outputCode.textContent = JSON.stringify(bundle, null, 2);
        outputPane.hidden = false;
        errorPane.hidden = true;
    }

    if (!form) return;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            const response = await fetch("/api/convert", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ hl7_text: textarea.value }),
            });
            const data = await response.json();
            if (!response.ok) {
                showError(data.error.category, data.error.message);
                return;
            }
            showResult(data.bundle);
        } catch (err) {
            showError("Network error", String(err));
        }
    });
});
