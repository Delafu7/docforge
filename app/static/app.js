document.querySelectorAll("form[data-endpoint]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = form.querySelector(".status");
    const fileInput = form.querySelector("input[type=file]");
    status.classList.remove("error");
    status.textContent = "";

    if (!fileInput.files.length) {
      return;
    }

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

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

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);

      status.textContent = "Done";
    } catch (err) {
      status.classList.add("error");
      status.textContent = "Network error";
    }
  });
});
