#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
import subprocess
import sys

from job import CREATOR_HOSTS
from package import current_blockers, load_package


LOCATION_PLATFORMS = {"xiaohongshu", "douyin"}
OPTIONS = {
    "xiaohongshu": ("公开范围", "原创与内容类型声明", "定时发布", "允许正文复制", "地点", "合集与活动"),
    "douyin": ("可见范围", "自主声明", "发布时间", "同时发布", "保存权限", "位置", "音乐与合集"),
    "bilibili": ("可见范围", "原创与 AI 辅助创作声明", "定时发布", "评论与精选评论", "话题与文集"),
}
SUGGESTIONS = {
    "xiaohongshu": "按现有发布范围和复制设置准备。地点、合集和活动按本篇需要配置",
    "douyin": "按现有发布范围和保存权限准备。音乐、定位、合集或同步发布有明确需要时再选",
    "bilibili": "按现有发布范围和评论设置准备。图片封面与文集按本篇用途确认",
}
COVERS = {
    "xiaohongshu": "按图序使用首图，核对笔记与封面预览",
    "douyin": "先核对默认封面是否为首图，有明确选择时再调整封面",
    "bilibili": "需要图片封面时，建议将已选首图设为自定义封面。保留默认时显示正文开头",
}


BROWSER_SCRIPT = r"""
const report = result => console.log('JARDEN_PREFLIGHT ' + JSON.stringify(result));
const spaces = await listTaskSpaces();
const original = spaces.find(entry => String(entry.id) === input.space);
if (!original || original.ownership !== 'agent') {
  report({
    space_status: original ? 'user_control' : 'space_missing',
    sites: {},
  });
} else {
  const task = await taskSpace(original.id);
  const tabs = await task.tabs();
  const sites = {};
  let spaceStatus = 'observed';
  for (const [platform, host] of Object.entries(input.targets)) {
    const matching = tabs.filter(tab => {
      try { return new URL(tab.url).origin === `https://${host}`; } catch { return false; }
    });
    if (matching.length !== 1) {
      sites[platform] = {session: matching.length ? 'multiple_tabs' : 'tab_missing'};
      continue;
    }
    try {
      const tab = matching[0];
      const page = tab.label ? task.page(tab.label) : tab.page;
      sites[platform] = await page.evaluate(async (platform) => {
        const main = document.body?.innerText ?? '';
        const frame = platform === 'bilibili' && [...document.querySelectorAll('iframe')].find(element =>
          element.getAttribute('src')?.startsWith('https://member.bilibili.com/york/read-'));
        const doc = frame?.contentDocument ?? document;
        const titleField = doc.querySelector('input[placeholder*="填写标题"], input[placeholder="添加作品标题"], textarea.title-input__inner');
        const bodyField = doc.querySelector('.tiptap.ProseMirror[contenteditable=true], .editor-comp-publish[contenteditable=true]');
        let signedIn = false;
        if (platform === 'xiaohongshu') {
          signedIn = !!document.querySelector('.user-info .name-box')?.textContent.trim();
        } else if (platform === 'douyin') {
          signedIn = Boolean(titleField && main.includes('预览图文')) ||
            (main.includes('抖音号：') && main.includes('发布图文')) ||
            (main.includes('发布图文') && !!doc.querySelector('input[type=file][multiple]'));
        } else {
          signedIn = !!frame?.contentDocument && (!!titleField || doc.body?.innerText.includes('草稿箱'));
        }
        const loginPrompt = /扫码登录|手机验证码登录|验证码登录|密码登录|登录后使用丰富/.test(main);
        let geolocation = 'unknown';
        try { geolocation = (await navigator.permissions.query({name: 'geolocation'})).state; } catch {}
        const filled = field => field ? (field.value.trim() ? 'filled' : 'empty') : 'not_visible';
        const fieldLabel = field => (field.closest('label') ?? field.parentElement?.parentElement)?.innerText.replace(/\s+/g, ' ').trim();
        const checkedLabels = [...doc.querySelectorAll('input:checked')].flatMap(field => {
          const label = fieldLabel(field);
          return label && field.getBoundingClientRect().width > 0 ? [label] : [];
        });
        const settings = {};
        if (bodyField) {
          if (platform === 'xiaohongshu') {
            settings.switches = Object.fromEntries([...doc.querySelectorAll('.custom-switch-card')].flatMap(card => {
              const label = ['原创声明', '允许正文复制', '定时发布'].find(name => card.innerText.trim().startsWith(name));
              const toggle = card.querySelector('.d-switch-simulator');
              return label && toggle ? [[label, toggle.classList.contains('checked')]] : [];
            }));
            settings.visibility = main.includes('公开可见') ? '公开可见' : null;
            settings.content_declaration = main.includes('笔记含AI合成内容') ? '笔记含AI合成内容' : null;
          } else if (platform === 'douyin') {
            const first = doc.querySelector('div[class^="img-"] > img');
            const cover = doc.querySelector('div[class^="cover-"][class*="clickable-"] > img');
            settings.first_image_as_cover = first && cover ? first.currentSrc === cover.currentSrc : null;
            settings.checked = checkedLabels;
            settings.content_declaration = doc.querySelector('[class^="selectText-"]')?.innerText.trim() ?? null;
          } else {
            const coverLabel = [...doc.querySelectorAll('input[type=checkbox]')].find(field =>
              fieldLabel(field)?.includes('未设置自定义封面'));
            settings.custom_cover = coverLabel?.checked ?? null;
            settings.checked = checkedLabels;
          }
        }
        return {
          session: loginPrompt ? 'login_required' : signedIn ? 'signed_in' : 'unknown',
          geolocation,
          path: location.pathname,
          editor: {
            title: filled(titleField),
            body: bodyField ? (bodyField.innerText.replaceAll('\u200b','').trim() ? 'filled' : 'empty') : 'not_visible',
          },
          settings,
        };
      }, platform);
      sites[platform].tab = page.label ?? null;
    } catch {
      sites[platform] = {session: 'check_interrupted'};
      spaceStatus = 'check_interrupted';
      break;
    }
  }
  report({space_status: spaceStatus, sites});
}
"""


def browser_state(space, platforms):
    if space is None:
        return {"space_status": "not_checked", "sites": {}}
    arguments = {"space": space, "targets": {platform: CREATOR_HOSTS[platform] for platform in platforms}}
    script = "const input = " + json.dumps(arguments) + ";\n" + BROWSER_SCRIPT
    try:
        result = subprocess.run(
            ["ego-browser", "nodejs"], input=script, encoding="utf-8",
            capture_output=True, check=True, timeout=30,
        )
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            if line.startswith("JARDEN_PREFLIGHT "):
                return json.loads(line.removeprefix("JARDEN_PREFLIGHT "))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return {"space_status": "check_interrupted", "sites": {}}


def report(jobs, space):
    plans = {}
    for job in jobs:
        packet = load_package(job)
        for platform, content in packet["platforms"].items():
            if platform in plans:
                raise ValueError(f"同一平台有多份内容包，请指定本次版本 {platform}")
            first = content["images"][0]
            plans[platform] = {
                "fingerprint": packet["fingerprint"],
                "title": content["title"],
                "body_chars": len(content["body"].strip()),
                "topics": content["topics"],
                "image_count": len(content["images"]),
                "cover": {"candidate": first["path"], "suggestion": COVERS[platform]},
                "review_options": OPTIONS[platform],
                "suggested_setup": SUGGESTIONS[platform],
                "source_settings": content["settings"],
                "package_blockers": current_blockers(packet, platform),
            }
    observed = browser_state(space, plans)
    for platform, plan in plans.items():
        site = observed["sites"].get(platform, {"session": "not_checked"})
        plan["browser"] = site
        actions = []
        if site["session"] == "login_required":
            actions.append("在官方创作页面登录")
        elif site["session"] != "signed_in":
            actions.append("确认当次创作页面与登录账号")
        if platform in LOCATION_PLATFORMS and site.get("geolocation") in ("prompt", "unknown", None):
            actions.append("本平台传图后曾请求位置，提前确认站点权限。需要地点时由用户允许")
        if site.get("editor", {}).get("title") == "empty" and plan["title"]:
            actions.append("当前编辑页标题为空，需要填写并回读")
        if site.get("editor", {}).get("body") == "empty" and plan["body_chars"]:
            actions.append("当前编辑页正文为空，需要填写并回读")
        plan["attention"] = actions
    return {"space_status": observed["space_status"], "platforms": plans}


def main():
    parser = argparse.ArgumentParser(description="一次核对所选图文平台的素材选项、登录与位置权限")
    parser.add_argument("jobs", nargs="+", type=Path)
    parser.add_argument("--space", help="已接管并打开目标平台的 Ego Lite 任务空间")
    args = parser.parse_args()
    try:
        print(json.dumps(report(args.jobs, args.space), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
