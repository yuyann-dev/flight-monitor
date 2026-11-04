"""Crawler package.

当前唯一可用数据源：飞猪 flyai CLI（FliggyCrawler）。
其余平台因反爬或资质限制无法稳定获取，已移除。
"""
from app.crawlers.fliggy_api import FliggyCrawler

__all__ = ["FliggyCrawler"]
