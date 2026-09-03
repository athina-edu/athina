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

__all__ = ('generate_llm_feedback',)


def generate_llm_feedback(configuration, student_code, test_results, test_descriptions,
                          logger=None):
    """Generate AI guidance for a student's submission.

    Args:
        configuration: Configuration object with llm_* settings
        student_code: dict of {filename: content}
        test_results: string of test output
        test_descriptions: list of human-readable test descriptions
        logger: optional Logger instance

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

    # Build the user message
    user_message = build_student_message(student_code, test_results, test_descriptions)
    system_prompt = get_system_prompt()

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


def read_student_code(student_code_dir, max_files=20):
    """Read student source code files from a directory.

    Returns dict of {filename: content} for common code files.
    Skips hidden files, .git, __pycache__, node_modules, etc.
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
