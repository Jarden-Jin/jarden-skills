#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
import subprocess
import sys

from job import run as record_job
from package import current_blockers, load_package


BROWSER_SCRIPT = r"""
const task = await taskSpace(options.space);
const page = task.page(options.tab);
const context = await page.evaluate(() => {
  const hasImageTab = [...document.querySelectorAll('.creator-tab.active')].some(element => {
    if (element.textContent.trim() !== '上传图文') return false;
    const box = element.getBoundingClientRect();
    return box.width > 0 && box.height > 0 &&
      element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
  });
  const inputs = [...document.querySelectorAll('input[type=file][multiple]')];
  let hasImageInput = false;
  if (inputs.length === 1) {
    const formats = inputs[0].accept.toLowerCase().split(',').map(value => value.trim());
    hasImageInput = ['.jpg', '.png', '.webp'].every(ext => formats.includes(ext)) && !inputs[0].files?.length;
  }
  return {
    url: location.href,
    account: document.querySelector('.user-info .name-box')?.textContent.trim(),
    hasImageTab,
    hasImageInput,
    hasTitle: [...document.querySelectorAll('input,textarea')].some(element => element.placeholder.includes('标题')),
  };
});
if (new URL(context.url).origin !== 'https://creator.xiaohongshu.com' ||
    context.account !== options.account || !context.hasImageTab || !context.hasImageInput || context.hasTitle) {
  throw new Error('图文入口、账号或空白上传表单不匹配，请先回读原页面');
}
if (!options.images) {
  console.log('JARDEN_XHS ' + JSON.stringify(context));
} else {
  const fs = await import('node:fs/promises');
  const crypto = await import('node:crypto');
  for (const image of options.images) {
    const hash = crypto.createHash('sha256').update(await fs.readFile(image.path)).digest('hex');
    if (hash !== image.sha256) throw new Error('图片在预检后发生变化');
  }
  await page.setInputFiles('input[type=file][multiple]', options.images.map(image => image.path));
  console.log(JSON.stringify({status:'media_selected',ready:false,next:'回读上传结果，继续填写并核对标题、正文、话题、封面和发布选项'}));
  const opening = await page.waitForFunction(({count, account}) =>
    document.querySelectorAll('.format-img img.preview').length === count &&
    document.querySelector('.user-info .name-box')?.textContent.trim() === account &&
    !!document.querySelector('input[placeholder*="填写标题"]') &&
    !!document.querySelector('.tiptap.ProseMirror[contenteditable=true]'),
  {count:options.images.length, account:options.account}, {timeout:15000});
  if (opening) {
    const title = 'input[placeholder*="填写标题"]';
    const editor = '.tiptap.ProseMirror[contenteditable=true]';
    const readCopy = ({title, editor}) => ({
      title: document.querySelector(title).value,
      body: [...document.querySelector(editor).children].map(node => node.textContent).join('\n\n').trimEnd(),
    });
    const before = await page.evaluate(readCopy,{title,editor});
    if (before.title || before.body) throw new Error('图文编辑器已有文字，请先核对原稿');
    await page.fill(`loc=css:${title}`,options.title);
    if (options.body.trimEnd()) {
      await page.focus(`loc=css:${editor}`);
      const escape = text => text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
      const text = options.body.trimEnd();
      const html = text.split(/\r?\n\r?\n/).map(part => `<p>${escape(part).replace(/\r?\n/g,'<br>')}</p>`).join('');
      await page.keyboard.paste({text,html});
    }
    const after = await page.evaluate(({title, editor}) => ({
      title: document.querySelector(title).value,
      body: [...document.querySelector(editor).children].map(node => node.innerText).join('\n\n').trimEnd(),
    }),{title,editor});
    if (after.title !== options.title || after.body !== options.body.replaceAll('\r\n','\n').trimEnd()) {
      throw new Error('标题或正文回读未通过，请检查原编辑页');
    }
    console.log(JSON.stringify({status:'copy_filled',ready:false,next:'核对实际图片、话题、封面和发布选项'}));
  }
  console.log(await page.snapshot({scope: 'full_page'}));
}
"""


def ego_script(options, capture=False):
    script = "const options = " + json.dumps(options) + ";\n" + BROWSER_SCRIPT
    result = subprocess.run(
        ["ego-browser", "nodejs"], input=script, encoding="utf-8",
        capture_output=capture, check=True,
    )
    if capture:
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            if line.startswith("JARDEN_XHS "):
                return json.loads(line.removeprefix("JARDEN_XHS "))
        raise ValueError("小红书选图入口未返回可核对状态")


def main():
    parser = argparse.ArgumentParser(description="在空白小红书图文页传入图片并填写已确认文案")
    parser.add_argument("job", type=Path)
    parser.add_argument("--space", type=int, required=True)
    parser.add_argument("--tab", required=True)
    parser.add_argument("--account", required=True)
    args = parser.parse_args()
    try:
        payload = load_package(args.job)
        content = payload["platforms"]["xiaohongshu"]
        if current_blockers(payload, "xiaohongshu"):
            raise ValueError("小红书仍有内容包缺项")
        options = {"space": args.space, "tab": args.tab, "account": args.account}
        context = ego_script(options, capture=True)
        record_job(argparse.Namespace(
            command="begin", job=args.job, platform="xiaohongshu",
            space=str(args.space), tab=args.tab, account=args.account, url=context["url"],
        ))
        if load_package(args.job)["fingerprint"] != payload["fingerprint"]:
            raise ValueError("任务内容包在登记期间发生变化")
        ego_script({**options, "images": content["images"], "title": content["title"], "body": content["body"]})
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"请查原任务状态 {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
