import io
import docx
import fitz
import json
import base64
import chardet
import pdfplumber
import pandas as pd
from PIL import Image
from flask import current_app


class FileOperation:

    @staticmethod
    def extract_images_from_pdf(pdf_path, is_content):
        if not is_content:
            return []
        with fitz.open(stream=pdf_path) as doc:
            base64_img = []
            for page_index in range(len(doc)):
                if page_index not in is_content:
                    continue
                # if img:= doc[page_index].get_images(full=True):
                #     xref = img[-1][0]
                #     base_image = doc.extract_image(xref)
                #     image_bytes = base_image["image"]
                #     img = Image.open(io.BytesIO(image_bytes))
                #     buffered = io.BytesIO()
                #     img.save(buffered, format="PNG")
                #     img = base64.b64encode(buffered.getvalue()).decode()
                #     base64_img.append(img)
                # else:
                # page = doc[page_index]
                # pix = page.get_pixmap(dpi=500)
                # img = Image.open(io.BytesIO(pix.tobytes("png")))
                # buffered = io.BytesIO()
                # img.save(buffered, format="PNG")
                # img = base64.b64encode(buffered.getvalue()).decode()
                # base64_img.append(img)
                # 建议改 dpi 为 150 或用缩放矩阵控制分辨率
                pix = page.get_pixmap(dpi=150)

                # 直接拿 PNG 字节，不要再二次转换
                img_bytes = pix.tobytes("png")

                # 转 base64
                img_b64 = base64.b64encode(img_bytes).decode()
                base64_img.append(img_b64)
        return base64_img

    @staticmethod
    def extract_text_from_pdf(pdf_path):
        pdf_bytes = io.BytesIO(pdf_path)
        is_content = []
        final_text = []
        tables = []
        df = ""
        with pdfplumber.open(pdf_bytes) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if table and any(any(once) for once in table):
                    tables.append(table)
                    table_bboxes = [pos.bbox for pos in page.find_tables()]
                    filtered_text = page.extract_words()
                    for word in filtered_text:
                        x0, y0, x1, y1 = word["x0"], word["top"], word["x1"], word["bottom"]
                        inside_table = any(
                            t_x0 <= x0 <= t_x1 and t_y0 <= y0 <= t_y1
                            for (t_x0, t_y0, t_x1, t_y1) in table_bboxes
                        )
                        if not inside_table:
                            final_text.append(word["text"])
                else:
                    text = page.extract_text()
                    if text and len(text) > 10:
                        final_text.append(text)
                    else:
                        is_content.append(page.page_number - 1)
            if tables:
                try:
                    df = "\n".join(pd.DataFrame(table[1:], columns=table[0]).to_json(force_ascii=False) for table in tables)
                except:
                    df = json.dumps(tables)
            if final_text:
                final_text = " ".join(final_text) + "\n"
            else:
                final_text = ""
        return final_text + df, is_content

    @staticmethod
    def extract_text_from_word(docx_path):
        doc = docx.Document(docx_path)
        text = "\n".join([p.text for p in doc.paragraphs])
        tables = []
        df = ""

        for table in doc.tables:
            table_data = []
            for row in table.rows:
                row_data = [cell.text.strip() for cell in row.cells]
                table_data.append(row_data)
            if table_data:
                tables.append(table_data)
        if tables:
            try:
                df = pd.DataFrame(tables[0][1:], columns=tables[0][0]).to_json(force_ascii=False)
            except:
                df = json.dumps(tables, ensure_ascii=False)
        return text.strip() + df

    @staticmethod
    def extract_images_from_word(docx_path):
        doc = docx.Document(docx_path)
        base64_img = []
        for rel in doc.part.rels:
            if "image" in doc.part.rels[rel].target_ref:
                image = doc.part.rels[rel].target_part.blob
                img = Image.open(io.BytesIO(image))
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                # save pages
                img_b64 = base64.b64encode(buffered.getvalue()).decode()
                base64_img.append(img_b64)
        return base64_img

    @staticmethod
    def check_pdf(pdf_path):
        with fitz.open(stream=pdf_path) as doc:
            for page_index in range(len(doc)):
                if doc[page_index].get_images(full=True) or not doc[page_index].get_text("text"):
                    page = doc[page_index]
                    pix = page.get_pixmap(dpi=500)
                    rect = page.rect
                    page.clean_contents()
                    page.insert_image(rect, pixmap=pix)
            pdf_buffer = io.BytesIO()
            doc.save(pdf_buffer, garbage=4, deflate=True)
            pdf_byte_path = pdf_buffer.getvalue()
        return pdf_byte_path

    @staticmethod
    def extract_picture(picture_path):
        base64_img = []
        img = Image.open(picture_path)
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img = base64.b64encode(buffered.getvalue()).decode()
        base64_img.append(img)
        return base64_img

    def __call__(self, username: str, attachment_names: list):
        if not isinstance(attachment_names, list):
            return {"message": "Invalid attachment_names format", "status": 400}
        else:
            results = {}
            for attachment_name in attachment_names:
                file_extension = attachment_name.rsplit(".", 1)[1].lower()
                blob_client = current_app.container_client.get_blob_client(f"{username}/{attachment_name}")
                stream = blob_client.download_blob().readall()
                file_stream = io.BytesIO(stream)
                encoding = chardet.detect(stream)["encoding"]

                if file_extension == "txt":
                    results[attachment_name] = {
                        "text": stream.decode(encoding),
                        "images": []
                    }

                elif file_extension == "csv":
                    df = pd.read_csv(file_stream, encoding=encoding)
                    results[attachment_name] = {
                        "text": df.to_json(force_ascii=False),
                        "images": []
                    }

                elif file_extension == "json":
                    results[attachment_name] = {
                        "text": stream.decode(encoding),
                        "images": []
                    }

                elif file_extension in ["xlsx", "xls"]:
                    try:
                        if file_extension == "xlsx":
                            df = pd.read_excel(file_stream, engine="openpyxl")
                        elif file_extension == "xls":
                            # 需要安装 pip install xlrd==1.2.0
                            df = pd.read_excel(file_stream, engine="xlrd")
                        else:
                            raise ValueError("Unsupported Excel file type")
                        results[attachment_name] = {
                            "text": df.to_json(force_ascii=False),
                            "images": []
                        }
                    except Exception as e:
                        results[attachment_name] = {
                            "text": f"Failed to read Excel file: {str(e)}",
                            "images": []
                        }

                # elif file_extension == "pdf":
                #     pdf_text, page_num = self.extract_text_from_pdf(stream)
                #     pdf_images = self.extract_images_from_pdf(stream, page_num)
                #     results[attachment_name] = {
                #         "text": pdf_text,
                #         "images": pdf_images
                #     }
                elif file_extension == "pdf":
                    pdf_stream = io.BytesIO(stream)  # 二进制转 BytesIO
                    pdf_text, page_num = self.extract_text_from_pdf(stream)
                    pdf_images = self.extract_images_from_pdf(pdf_stream, page_num)
                    results[attachment_name] = {
                        "text": pdf_text,
                        "images": pdf_images
                    }

                elif file_extension == "docx":
                    word_text = self.extract_text_from_word(file_stream)
                    try:
                        word_images = self.extract_images_from_word(file_stream)
                    except:
                        word_images = []
                    results[attachment_name] = {
                        "text": word_text,
                        "images": word_images
                    }

                elif file_extension in ["jpg", "jpeg", "png"]:
                    images = self.extract_picture(file_stream)
                    results[attachment_name] = {
                        "text": "",
                        "images": images
                    }

                else:
                    results[attachment_name] = {
                        "text": f"Unsupported file type: {file_extension}",
                        "images": []
                    }

        return results


if __name__ == '__main__':
    file_get = FileOperation()
    content = file_get("./", "223.pdf")

