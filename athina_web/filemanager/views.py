from django.shortcuts import render
from django.conf import settings
import os
from django.contrib.auth.decorators import login_required
from django.utils.text import get_valid_filename
from . import utils
from .forms import FileFieldForm
from django.shortcuts import redirect
import datetime
import git


@login_required
def index(request, **kwargs):
    inner_path = kwargs.get('inner_path', None)
    inner_path, inner_path_hyphened, full_path = utils.inner_path_process(inner_path, request.user.id)
    results = []

    # Refresh git repo — always reset to remote (local changes are discarded)
    try:
        repo = git.Repo(full_path)
        repo.remote().fetch()
        repo.git.reset("--hard", "origin/master")
    except (git.exc.InvalidGitRepositoryError, git.exc.GitCommandError, git.exc.NoSuchPathError):
        pass

    files = os.listdir(full_path)
    for file in files:
        if os.path.isfile("%s/%s" % (full_path, file)):
            isfile = True
        else:
            isfile = False
        if inner_path == "":
            path = utils.slashes_encode("%s" % file)
        else:
            path = utils.slashes_encode("%s/%s" % (inner_path, file))
        date_modified = datetime.datetime.fromtimestamp(int(os.path.getmtime("%s/%s" % (full_path, file)))).isoformat()
        if file != ".git":  # hide .git
            results.append({"name": file, "path": path, "isfile": isfile, "date_modified": date_modified})

    previous_dir_path = "|".join(inner_path_hyphened.split("|")[:len(inner_path_hyphened.split("|"))-1])
    if previous_dir_path != "":
        results.append({"name": "..", "path": previous_dir_path, "isfile": False, "date_modified": ""})
    results = sorted(results, key=lambda x: x['isfile'])
    return render(request, 'filemanager/filemanager.html', {"results": results, "inner_path": inner_path,
                                                            "inner_path_hyphened": inner_path_hyphened})


@login_required
def upload(request, **kwargs):
    inner_path = kwargs.get('inner_path', None)
    inner_path, inner_path_hyphened, full_path = utils.inner_path_process(inner_path, request.user.id)
    form = FileFieldForm(request.POST, request.FILES)
    if request.method != "POST":
        return render(request, 'filemanager/upload.html', {"form": form, "inner_path_hyphened": inner_path_hyphened,
                                                           "inner_path": inner_path})
    else:
        files = request.FILES.getlist('file_field')
        # TODO: Prompt for an overwrite question, by default for now we overwrite
        if form.is_valid():
            # Save files on disk
            for file in files:
                safe_name = get_valid_filename(file.name)
                dest_path = os.path.join(full_path, safe_name)
                # Double-check: resolved path must still be within full_path
                if not os.path.normpath(dest_path).startswith(os.path.normpath(full_path)):
                    continue
                with open(dest_path, 'wb+') as destination:
                    for chunk in file.chunks():
                        destination.write(chunk)
        if inner_path != "":
            return redirect('filemanager:index', inner_path=inner_path_hyphened)
        else:
            return redirect('filemanager:index')


@login_required
def new_folder(request, **kwargs):
    inner_path = kwargs.get('inner_path', None)
    inner_path, inner_path_hyphened, user_dir = utils.inner_path_process(inner_path, request.user.id)

    return 1


@login_required
def view_file(request, **kwargs):
    inner_path = kwargs.get('inner_path', None)
    inner_path, inner_path_hyphened, full_path = utils.inner_path_process(inner_path, request.user.id)
    with open(full_path, 'rb') as f:
        file_contents = f.read().decode("utf-8")
    reverse_view = kwargs.get('reverse', None)
    if reverse_view == "reverse":
        # TODO: reverse the printing of the view here
        file_contents = "\n".join(reversed(file_contents.split("\n")))
        pass
    return render(request, 'filemanager/view_file.html', {"file_contents": file_contents, "inner_path": inner_path,
                                                          "inner_path_hyphened": inner_path_hyphened})

