# -*- coding: utf-8 -*-
"""
Athina LLM Client — OpenAI-compatible API integration for student feedback.

Sends student code + test results to an LLM and returns guidance.
Uses only the `requests` library (already a dependency) — no OpenAI SDK needed.
"""
import json
import os
import re

from athina.url import request_url
from athina.llm_agent_prompt import get_system_prompt, build_student_message, sanitize_output

__all__ = ('generate_llm_feedback', 'read_student_code', 'parse_test_descriptions')

#: Files above this size are not read for LLM feedback. Database courses
#: routinely commit a dump (the employees sample is 168 MB uncompressed); the
#: prompt budget would discard the text anyway, so reading it only costs memory.
DEFAULT_MAX_STUDENT_FILE_BYTES = 256 * 1024


def generate_llm_feedback(configuration, student_code, test_results, test_descriptions,
                          logger=None, test_records=None):
    """Generate AI guidance for a student's submission.

    Args:
        configuration: Configuration object with llm_* settings
        student_code: dict of {filename: content}
        test_results: string of test output
        test_descriptions: list of human-readable test descriptions
        logger: optional Logger instance
        test_records: optional parsed per-test records (athina.test_report).
            These are the compact, structured verdicts already shown in the grade
            report; when supplied they replace most of the raw log in the prompt.

    Returns:
        String with guidance text, or None if LLM is not configured/failed
    """
    if not getattr(configuration, 'llm_enabled', False):
        return None

    endpoint_url = getattr(configuration, 'llm_endpoint_url', '').rstrip('/')
    api_key = getattr(configuration, 'llm_api_key', '')
    model = getattr(configuration, 'llm_model', 'gpt-4o-mini')

    if not endpoint_url or not api_key:
        if logger:
            logger.logger.debug("LLM feedback skipped: no endpoint or API key configured")
        return None

    # Build the user message. The budgets come from the assignment config so a
    # course with chatty test scripts can be reined in without a code change.
    user_message = build_student_message(
        student_code, test_results, test_descriptions,
        max_file_chars=getattr(configuration, 'llm_max_file_chars', None),
        max_code_chars=getattr(configuration, 'llm_max_code_chars', None),
        max_test_chars=getattr(configuration, 'llm_max_test_chars', None),
        test_records=test_records)
    system_prompt = get_system_prompt()

    if logger:
        logger.logger.debug("LLM prompt: %d chars input (%d code files)" % (
            len(user_message), len(student_code or {})))

    # OpenAI-compatible chat completions endpoint
    url = "%s/chat/completions" % endpoint_url

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % api_key,
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,  # Low temperature for consistent, focused responses
        "max_tokens": 8192,  # Needs headroom for reasoning models (e.g. MiMo, o1) that
                             # use most tokens for internal reasoning before output
    }

    try:
        if logger:
            logger.logger.info("Generating LLM feedback with model: %s" % model)

        # Use raw requests to avoid needing the openai SDK
        import requests
        resp = requests.post(url, headers=headers, json=payload, timeout=120)

        if resp.status_code != 200:
            if logger:
                logger.logger.error("LLM API returned status %d: %s" % (
                    resp.status_code, resp.text[:200]))
            return None

        data = resp.json()
        message = data["choices"][0]["message"]
        guidance = message.get("content") or ""

        # Reasoning models (MiMo, o1, etc.) may put the actual response in
        # "reasoning_content" while leaving "content" empty.  Fall back to
        # the reasoning content so we still return something useful.
        if not guidance.strip() and message.get("reasoning_content"):
            if logger:
                logger.logger.debug("Content empty; falling back to reasoning_content")
            guidance = message["reasoning_content"]

        # Defense-in-depth: sanitize output
        guidance = sanitize_output(guidance)

        if logger:
            logger.logger.info("LLM feedback generated (%d chars)" % len(guidance))

        return guidance if guidance.strip() else None

    except Exception as e:
        if logger:
            logger.logger.error("LLM feedback generation failed: %s" % str(e))
        return None


def read_student_code(student_code_dir, max_files=20,
                      max_file_bytes=DEFAULT_MAX_STUDENT_FILE_BYTES):
    """Read student source code files from a directory.

    Returns dict of {filename: content} for common code files.
    Skips hidden files, .git, __pycache__, node_modules, etc.

    Files larger than `max_file_bytes` are skipped rather than read: a committed
    database dump can be hundreds of megabytes, and the LLM prompt only keeps a
    small slice of it anyway. Pass 0 (or a negative value) to disable the check.
    """
    code_files = {}
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env',
                 '.tox', 'dist', 'build', '.eggs'}
    code_extensions = {'.py', '.r', '.R', '.java', '.c', '.cpp', '.h', '.js',
                       '.ts', '.go', '.rs', '.rb', '.sh', '.sql', '.html', '.css'}

    if not os.path.isdir(student_code_dir):
        return code_files

    for root, dirs, files in os.walk(student_code_dir):
        # Skip hidden and无关 directories
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]

        for fname in sorted(files):
            if fname.startswith('.'):
                continue
            _, ext = os.path.splitext(fname)
            if ext.lower() not in code_extensions:
                continue

            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, student_code_dir)

            try:
                if max_file_bytes and max_file_bytes > 0:
                    if os.path.getsize(fpath) > max_file_bytes:
                        continue
                with open(fpath, 'r', errors='replace') as f:
                    content = f.read()
                code_files[rel_path] = content
            except (OSError, IOError):
                continue

            if len(code_files) >= max_files:
                return code_files

    return code_files


def parse_test_descriptions(configuration):
    """Extract human-readable test descriptions from the YAML config.

    These are the test_scripts names and weights — NOT the actual code.
    """
    descriptions = []
    scripts = getattr(configuration, 'test_scripts', [])
    weights = getattr(configuration, 'test_weights', [])

    for i, script in enumerate(scripts):
        weight = weights[i] if i < len(weights) else 0
        descriptions.append("Test %d (weight %.0f%%): %s" % (
            i + 1, weight * 100, script))

    return descriptions
