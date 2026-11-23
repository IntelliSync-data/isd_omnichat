/** @odoo-module **/

/** Read files from an <input type="file"> as ir.attachment values. */
export function readFiles(files) {
    return Promise.all(
        [...files].map(
            (file) =>
                new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () =>
                        resolve({ name: file.name, mimetype: file.type, datas: reader.result.split(",")[1] });
                    reader.onerror = reject;
                    reader.readAsDataURL(file);
                })
        )
    );
}
