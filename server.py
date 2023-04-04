#!/usr/bin/env python
import io
import json
import math
import os
import tempfile
import uuid
import zipfile

import PIL.Image
import flask
from flask import Flask, request, render_template
from werkzeug.datastructures import FileStorage


app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
app.config["JSON_PRETTYPRINT_REGULAR"] = True


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


def read_info_from_image_stealth(image: PIL.Image) -> str:
    if "parameters" in image.text:
        return image.text["parameters"]

    # trying to read stealth pnginfo
    width, height = image.size
    pixels = image.load()

    binary_data = ""
    buffer = ""
    index = 0
    sig_confirmed = False
    confirming_signature = True
    reading_param_len = False
    reading_param = False
    read_end = False
    for x in range(width):
        for y in range(height):
            r, g, b, a = pixels[x, y]
            buffer += str(a & 1)
            pixels[x, y] = (r, g, b, 0)
            if confirming_signature:
                if index == len("stealth_pnginfo") * 8 - 1:
                    if buffer == "".join(format(byte, "08b") for byte in "stealth_pnginfo".encode("utf-8")):
                        confirming_signature = False
                        sig_confirmed = True
                        reading_param_len = True
                        buffer = ""
                        index = 0
                    else:
                        read_end = True
                        break
            elif reading_param_len:
                if index == 32:
                    param_len = int(buffer, 2)
                    reading_param_len = False
                    reading_param = True
                    buffer = ""
                    index = 0
            elif reading_param:
                if index == param_len:
                    binary_data = buffer
                    read_end = True
                    break
            else:
                # impossible
                read_end = True
                break

            index += 1
        if read_end:
            break

    if sig_confirmed and binary_data != "":
        # Convert binary string to UTF-8 encoded text
        result = bytearray(int(binary_data[i : i + 8], 2) for i in range(0, len(binary_data), 8)).decode(
            "utf-8", errors="ignore"
        )

        if result.startswith("{") and result.endswith("}"):
            result = json.loads(result)
        return result

    return ""


def add_stealth_pnginfo(image: PIL.Image) -> PIL.Image:

    width, height = image.size
    image.putalpha(255)
    pixels = image.load()
    str_parameters = image.text.get("parameters")
    if not str_parameters:
        if "prompt" in image.text and "workflow" in image.text:
            str_parameters = json.dumps(image.text)
        else:
            return None
    # prepend signature
    signature_str = "stealth_pnginfo"

    binary_signature = "".join(format(byte, "08b") for byte in signature_str.encode("utf-8"))

    binary_param = "".join(format(byte, "08b") for byte in str_parameters.encode("utf-8"))

    # prepend length of parameters, padded to 32 digits
    param_len = len(binary_param)
    binary_param_len = format(param_len, "032b")

    binary_data = binary_signature + binary_param_len + binary_param
    index = 0
    for x in range(width):
        for y in range(height):
            if index < len(binary_data):
                r, g, b, a = pixels[x, y]

                # Modify the alpha value's least significant bit
                a = (a & ~1) | int(binary_data[index])

                pixels[x, y] = (r, g, b, a)
                index += 1
            else:
                break

    return image


def send_pillow_image_file(file, image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    buffer.seek(0)
    return flask.send_file(
        buffer,
        mimetype="image/png",
        as_attachment=True,
        download_name=f'{file.filename.split(".")[0]}_with_metadata.png',
    )


@app.route("/", methods=["POST"])
def load():
    files: list[FileStorage] = request.files.getlist("files")

    file_text_list = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_file_path = os.path.join(tmp_dir, f"{uuid.uuid4()}.zip")
        with zipfile.ZipFile(zip_file_path, "w", compression=zipfile.ZIP_STORED) as zip_file:
            for file in files:
                image = PIL.Image.open(file)

                if request.form.get("checkbox"):
                    if image.mode == "RGBA":
                        text_content = read_info_from_image_stealth(image)
                    else:
                        text_content = image.text.get("parameters") or image.text
                    if isinstance(text_content, dict):
                        text_content = "\n\n".join(f"{key}:\n{value}" for key, value in text_content.items())
                    file_text_list.append(
                        dict(
                            text_content=text_content,
                            name=file.filename,
                        )
                    )
                    continue

                if image.mode == "RGBA":
                    new_metadata = read_info_from_image_stealth(image)
                    if isinstance(new_metadata, dict):
                        for key, value in new_metadata.items():
                            image.text[key] = value
                    else:
                        image.text["parameters"] = new_metadata

                    if request.form.get("checkbox"):
                        if isinstance(new_metadata, dict):
                            new_metadata = "\n\n".join(f"{key}:\n{value}" for key, value in new_metadata.items())

                        file_text_list.append(
                            dict(
                                text_content=new_metadata,
                                name=file.filename,
                            )
                        )
                        continue
                    if len(files) == 1:
                        return send_pillow_image_file(file, image)
                    else:
                        buffer = io.BytesIO()
                        image.save(buffer, format="PNG", optimize=False)
                        buffer.seek(0)
                        zip_file.writestr(
                            f'{file.filename.split(".")[0]}_with_metadata.png',
                            buffer.read(),
                        )
                else:
                    image = add_stealth_pnginfo(image)
                    if not image:
                        return render_template(
                            "index.html",
                            errors=[f"ERROR one of the images was missing metadata {file.filename}"],
                        )
                    if not image.text:
                        return render_template(
                            "index.html",
                            errors=[f"Failed to get original text from {file.filename}."],
                        )
                    if len(files) == 1:
                        return send_pillow_image_file(file, image)
                    else:
                        buffer = io.BytesIO()
                        image.save(buffer, format="PNG", optimize=False)
                        buffer.seek(0)
                        zip_file.writestr(
                            f'{file.filename.split(".")[0]}_with_metadata.png',
                            buffer.read(),
                        )
        if request.form.get("checkbox"):
            return render_template("index.html", file_list=enumerate(file_text_list))
        else:
            return flask.send_file(
                zip_file_path,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"all_files_processed.zip",
            )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT") or 7090),
        debug=os.getenv("DEBUG") == "True",
    )
