document.querySelectorAll("form[data-endpoint]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = form.querySelector(".status");
    const fileInput = form.querySelector("input[name=file]");
    status.classList.remove("error");
    status.textContent = "";

    if (!fileInput.files.length) {
      return;
    }

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    const imagesInput = form.querySelector("input[name=images]");
    if (imagesInput) {
      Array.from(imagesInput.files).forEach((imageFile) => {
        formData.append("images", imageFile);
        formData.append("image_paths", imageFile.webkitRelativePath || imageFile.name);
      });
    }

    form.querySelectorAll("input[type=text], input[type=checkbox]").forEach((input) => {
      if (input.type === "checkbox") {
        formData.append(input.name, input.checked);
      } else if (input.value) {
        formData.append(input.name, input.value);
      }
    });

    status.textContent = "Converting...";

    try {
      const response = await fetch(form.dataset.endpoint, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({ detail: response.statusText }));
        status.classList.add("error");
        status.textContent = body.detail || "Conversion failed";
        return;
      }

      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const filename = match ? match[1] : "download";
      const reportHeader = response.headers.get("X-Conversion-Report");

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);

      const summary = _summarize(reportHeader);
      status.textContent = summary ? `Done. ${summary}` : "Done";
    } catch (err) {
      status.classList.add("error");
      status.textContent = "Network error";
    }
  });
});

function _summarize(reportHeader) {
  if (!reportHeader) {
    return "";
  }
  try {
    const report = JSON.parse(reportHeader);
    return (
      `${report.images_embedded} image(s) embedded ` +
      `(${report.images_from_upload} from upload, ${report.images_downloaded} downloaded), ` +
      `${report.images_skipped.length} skipped.`
    );
  } catch (err) {
    return "";
  }
}
