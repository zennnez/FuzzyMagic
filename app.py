"""
This module is in effect the controller of the whole web-based application.
It relies on Flask
"""


import os
import pandas as pd
from flask import Flask, render_template, request, redirect, abort, flash, send_from_directory, session, url_for
from functools import wraps
from flask_session import Session
from werkzeug.utils import secure_filename
import random
from datetime import datetime
from string import ascii_lowercase
from fuzzyspreadsheets import generate_spreadsheet, generate_spreadsheets
from helpers import do_backend, write_errorlog, read_errorlog
from operator import attrgetter


# An instance of the Flask-class is our WSGI application
app = Flask(__name__)
app.secret_key = os.getenv("FUZZY_TOKEN", "fuzzykapass")

# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if os.getenv("FUZZY_TOKEN") and session.get("token") != os.getenv("FUZZY_TOKEN"):
            # Check if token is in query params
            token = request.args.get("token")
            if token == os.getenv("FUZZY_TOKEN"):
                session["token"] = token
                return f(*args, **kwargs)
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        token = request.form.get("token")
        if token == os.getenv("FUZZY_TOKEN", "fuzzykapass"):
            session["token"] = token
            return redirect(request.args.get("next") or "/")
        flash("Invalid token")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# Comment this block if you want sessions to be saved in flask_session instead of in a temp dir
TEMP_DIR = None
"""
from tempfile import mkdtemp
TEMP_DIR = mkdtemp()
app.config["SESSION_FILE_DIR"] = TEMP_DIR   # will use a temp folder instead of flask_session folder
"""

# Configure session to use filesystem (instead of signed cookies)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
#app.config["TEMPLATES_AUTO_RELOAD"] = True  # Reload templates when they are changed. If not set, it will be enabled in debug mode
Session(app)



# Upload configurations
app.config['DOWNLOAD_FOLDER'] = "downloads"
app.config['UPLOAD_FOLDER'] = "uploads"
app.config['MAX_CONTENT_PATH'] = 1024 * 1024 * 10  # Increase to 10 megabytes for Excel files
app.config['UPLOAD_EXTENSIONS'] = ['csv', 'xlsx']

# Create uploads and downloads folders if not exist
for dirpath in (app.config['DOWNLOAD_FOLDER'], app.config['UPLOAD_FOLDER']):
    if not os.path.exists(dirpath):
        os.makedirs(dirpath)


# Routes
@app.route("/", methods=['GET'])
@login_required
def index():
    return render_template("index.html")


@app.route("/generate", methods=['GET', 'POST'])
@login_required
def generate():
    if request.method == 'GET':
        return render_template("generate.html")
    # If POST
    w = 'detect' if request.form.get("detect") else 'merge' if request.form.get("merge") else None
    q = request.form.get("q")  # number of rows
    n_rows = int(q)

    # Construct folder name
    directory = "generate_{}_{}".format(datetime.now().strftime("%Y_%m_%d_%H_%M_%S"),
                                   str.join('', (ascii_lowercase[ix] for ix in random.choices(range(26), k=5))))
    #directory = os.path.join(app.config['DOWNLOAD_FOLDER'], directory)
    directory = os.path.join(app.root_path, app.config['DOWNLOAD_FOLDER'], directory)

    # Make dir
    os.mkdir(directory)

    # If 'detect'
    if w == "detect":
        filename = "generated_spreadsheet.csv"
        filepath = generate_spreadsheet(n_rows, directory=directory, filename=filename)
    # If 'merge'
    elif w == 'merge':
        from zipfile import ZipFile
        filepath1, filepath2 = generate_spreadsheets(n_rows, filename1="generated_spreadsheet1.csv", filename2="generated_spreadsheet2.csv", directory=directory)
        filename = "generated_spreadsheets.zip"

        # A bit hackish but does the job
        cwd = os.getcwd()
        os.chdir(directory)
        #zipfilepath = os.path.join(directory, filename)
        z = ZipFile(filename, mode='w')
        try:
            z.write("generated_spreadsheet1.csv")
            z.write("generated_spreadsheet2.csv")
        except Exception as err:
            abort(500, err)
        finally:
            z.close()
        # Delete the files
        for file in (filepath1, filepath2):
            if os.path.exists(file):
                os.remove(file)
        # Change the cwd back to its original
        os.chdir(cwd)
    # If not 'detect' and not 'merge'
    else: raise ValueError("must be 'detect' or 'merge'")
    # Send
    # Flask >= 2.3 expects 'path' instead of 'filename'
    return send_from_directory(directory=directory, path=filename, as_attachment=True)






@app.route("/detect", methods=['GET'])
@app.route("/merge", methods=['GET'])
@login_required
def detect_or_merge():
    path = str(request.path).lstrip('/')
    rs = render_template(path + ".html")    # path = request.path   # "/detect"

    # Delete the list of download_files so that the merge page loads normally the next time
    if session.get("download_files", None):
        del session["download_files"]
    return rs



@app.route('/upload', methods=['GET', 'POST'])
@app.route('/upload/<subroute>', methods=['GET', 'POST'])
@login_required
def upload(subroute=None):
    # If user types url "/upload"
    if request.method != 'POST':
        return redirect("/")

    # If POST
    f1 = request.files.get('file1', None)   # f1.content_length == 0 (if no file selected)
    f2 = request.files.get('file2', None)
    
    # Check for server-side files
    server_file1 = request.form.get('server_file1')
    server_file2 = request.form.get('server_file2')

    # Prepare files list for processing
    files_to_process = []
    
    # Handle first file
    if f1 and f1.filename != '':
        files_to_process.append(('upload', f1))
    elif server_file1:
        files_to_process.append(('server', server_file1))
    else:
        files_to_process.append((None, None))

    # Handle second file (only for merge)
    if subroute == "merge":
        if f2 and f2.filename != '':
            files_to_process.append(('upload', f2))
        elif server_file2:
            files_to_process.append(('server', server_file2))
        else:
            files_to_process.append((None, None))

    # Just in case
    if session.get("download_files"):
        del session["download_files"]

    # Case: user clicked "Generate spreadsheet(s)" (no files provided at all)
    if all(ftype is None for ftype, fval in files_to_process):
        try:
            session["download_files"] = do_backend(operation=subroute)
        except Exception as err:
            abort(500, err)
        return redirect("/" + subroute)

    # Case: the user didn't select enough file(s) for the operation
    if any(ftype is None for ftype, fval in files_to_process):
        msg = "You must select {k1:} csv/xlsx file{n:} or Generate {k2:}spreadsheet{n:}".format(
            n=('' if subroute == 'detect' else 's'),
            k1=('a' if subroute == 'detect' else "two"),
            k2=("a " if subroute == 'detect' else ''))
        flash(msg)
        return redirect("/" + subroute)

    # Case: the user provided two identical files (filenames)
    filenames = []
    for ftype, fval in files_to_process:
        if ftype == 'upload':
            filenames.append(fval.filename)
        else:
            filenames.append(fval)
            
    if len(set(filenames)) != len(filenames):
        flash("You must provide two different files")
        return redirect("/" + subroute)

    # Case: not a valid extension
    for ftype, fval in files_to_process:
        fname = fval.filename if ftype == 'upload' else fval
        if not os.path.splitext(fname)[-1][1:] in app.config['UPLOAD_EXTENSIONS']:
            flash("{k1:} input file{k2:} must have a csv or xlsx extension.".format(
                k1=("The" if subroute == 'detect' else "Both"),
                k2=('' if subroute == 'detect' else 's')))
            return redirect("/" + subroute)

    # Case: the user provided valid input file(s)
    filepaths = []
    prefix = "{}_{}_{}".format(subroute, datetime.now().strftime("%Y_%m_%d"),
                                      str.join('', (ascii_lowercase[ix] for ix in random.choices(range(26), k=5))))
    
    for (i, (ftype, fval)) in enumerate(files_to_process):
        if ftype == 'upload':
            filename = secure_filename(fval.filename)
            ext = os.path.splitext(filename)[-1][1:]
            path = os.path.join(app.config['UPLOAD_FOLDER'], "{}_{}".format(prefix, f"{i+1}_" if subroute == "merge" else '') + filename)
            fval.save(path)
            fval.close()
        else:
            # Server file
            server_filename = fval
            ext = os.path.splitext(server_filename)[-1][1:]
            # Copy server file to uploads to avoid modifying original or permission issues
            import shutil
            filename = secure_filename(server_filename)
            path = os.path.join(app.config['UPLOAD_FOLDER'], "{}_{}".format(prefix, f"{i+1}_" if subroute == "merge" else '') + filename)
            shutil.copy(server_filename, path)
        
        # Convert xlsx to csv if necessary
        if ext == 'xlsx':
            csv_path = path.replace('.xlsx', '.csv')
            try:
                df = pd.read_excel(path)
                df.to_csv(csv_path, index=False)
                os.remove(path) # remove original xlsx
                path = csv_path
            except Exception as e:
                flash(f"Error converting Excel file: {str(e)}")
                return redirect("/" + subroute)
        
        filepaths.append(path)

    # Try backend operation
    try: 
        session["download_files"] = do_backend(filepaths, operation=subroute, app=app)
    except Exception as err: 
        abort(500, err)

    # In any case
    return redirect("/" + subroute)



@app.route("/downloads/<directory>/<filename>", methods=['GET', 'POST'])
@login_required
def download(directory, filename):
    # Special case to download the README.md of fuzzyspreadsheets package
    if directory == "fuzzyspreadsheets" and filename == "README.md":
        directory = os.path.join(app.root_path, directory)
    # General case
    else:
        directory = os.path.join(app.root_path, app.config['DOWNLOAD_FOLDER'], directory)
    # Flask >= 2.3 expects 'path' instead of 'filename'
    return send_from_directory(directory=directory, path=filename)



@app.route("/about", methods=['GET', 'POST'])
@login_required
def about():
    if request.method == 'GET':
        return render_template("about.html")
    # If POST, download package
    directory = "package_{}_{}".format(datetime.now().strftime("%Y_%m_%d_%H_%M_%S"),
                                   str.join('', (ascii_lowercase[ix] for ix in random.choices(range(26), k=5))))
    packagename = "fuzzyspreadsheets"
    directory = os.path.join(app.root_path, app.config['DOWNLOAD_FOLDER'], directory)

    # Copy the folder
    from shutil import copytree, make_archive, rmtree
    copytree(packagename, os.path.join(directory, packagename))

    # Delete __pychache__
    if os.path.exists(os.path.join(os.path.join(directory, packagename), "__pycache__")):
        rmtree(os.path.join(os.path.join(directory, packagename), "__pycache__"))

    # Archive
    archive_path = make_archive(os.path.join(directory, packagename), format='zip', root_dir=directory, base_dir=packagename)

    # Delete folder
    rmtree(os.path.join(directory, packagename))

    # Download
    # Flask >= 2.3 expects 'path' instead of 'filename'
    return send_from_directory(directory=directory, path=packagename + ".zip", as_attachment=True)




# Error handling
@app.errorhandler(404)   # file / page not found error
@app.errorhandler(400)   # bad request / bad file extension
@app.errorhandler(500)   # Internal Server Error
def error(err):
    """
    Logs error and displays an apology page
    """

    # Construct log dictionary
    request_keys = ('base_url', 'endpoint', 'path', 'referrer', 'url', 'user_agent')
    err_keys = ('code', 'description', 'name')
    d = {'date': datetime.now().strftime("%d.%m.%Y %H:%M:%S")}

    for k in request_keys:
        try: v = attrgetter(k)(request)
        except Exception: continue
        else: d[k] = v

    for k in err_keys:
        try: v = attrgetter(k)(err)
        except Exception: continue
        else: d.update({k: v})

    # Write to log
    write_errorlog(d)

    # Render apology page
    return render_template("apology.html", err=err)  # {{err | safe}}




# When debugging during development (when deploying, comment this block out)
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8086)

if TEMP_DIR and os.path.exists(TEMP_DIR) and TEMP_DIR not in (os.getcwd(), "/"):   # just to be on the safe side
    from shutil import rmtree
    rmtree(TEMP_DIR)







