# -*- coding: utf-8 -*-
"""Backwards-compatibility shim for the plagiarism detection module.

The implementation lives in :mod:`athina.moss` (see that module for details).
This module used to hold a near-identical copy of it, which caused SonarCloud
to report ~10% duplication on new code. Import from ``athina.moss`` instead;
this shim exists only so existing imports keep working.
"""
from athina.moss import *  # noqa: F401,F403
from athina.moss import Plagiarism, plagiarism_checks_on_users  # noqa: F401

__all__ = ('plagiarism_checks_on_users', 'Plagiarism')
