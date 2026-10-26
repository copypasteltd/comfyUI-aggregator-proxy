"""Visible text geometry shared by UI fixtures and exact-installed checks."""

from __future__ import annotations

from playwright.sync_api import Page, expect

GEOMETRY = """el => {
  const tag = el.querySelector('.el-tag');
  const content = tag.querySelector('.el-tag__content');
  const range = document.createRange(); range.selectNodeContents(content);
  const box = x => ({left:x.left,right:x.right,top:x.top,bottom:x.bottom,width:x.width,height:x.height});
  const style = getComputedStyle(tag);
  return {text:content.textContent,card:box(el.getBoundingClientRect()),tag:box(tag.getBoundingClientRect()),
    text_box:box(range.getBoundingClientRect()),body:box(el.querySelector('.audit-retention-body').getBoundingClientRect()),
    transform:style.transform,opacity:Number(style.opacity),color:style.color,
    page_overflow:document.documentElement.scrollWidth > innerWidth + 2};
}"""


def retention_geometry(page: Page) -> dict:
    card = page.get_by_role("region", name="审计保留策略")
    expect(card.locator(".el-tag")).to_be_visible()
    metrics = card.evaluate(GEOMETRY)
    assert metrics["transform"] == "none" and metrics["opacity"] == 1
    text, tag, outer = metrics["text_box"], metrics["tag"], metrics["card"]
    assert text["width"] > 50 and text["height"] >= 12
    assert text["left"] >= tag["left"] - 1 and text["right"] <= tag["right"] + 1
    assert text["top"] >= tag["top"] - 1 and text["bottom"] <= tag["bottom"] + 1
    assert tag["left"] >= outer["left"] + 8 and tag["right"] <= outer["right"] - 8
    assert tag["bottom"] <= outer["bottom"] - 8
    assert not metrics["page_overflow"]
    return metrics
