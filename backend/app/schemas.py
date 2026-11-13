"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class WatermarkPayload(BaseModel):
    """明确水位类型与顺序：旧样例水位是不可比较文字时，认领区间前的必备步骤。"""

    水位类型: str
    当前水位: str | None = None
    水位顺序表: list[str] = Field(default_factory=list)


class ClaimPayload(BaseModel):
    """认领一个起止水位区间：实时轨从预留前沿起认，回扫轨起点由回退步数决定。"""

    track: str = "realtime"
    start: str | None = None
    end: str | None = None
    job: str | None = None


class ReceiptPayload(BaseModel):
    """批次回执：采集任务完成后凭批次号确认认领区间，重复回执不额外前移水位。"""

    批次号: str
    实际终点: str | None = None
    样本: list[dict[str, Any]] = Field(default_factory=list)



class SiteEntry(BaseModel):
    """采集站点明细结构。"""

    field_0: str | None = None  # 站点编号
    field_1: str | None = None  # 域名地址
    field_2: str | None = None  # 站点类型
    field_3: str | None = None  # 请求方式
    field_4: str | None = None  # 字符编码
    field_5: str | None = None  # 请求头模板
    field_6: str | None = None  # Cookie配置
    field_7: str | None = None  # 站点负责人

class TemplateEntry(BaseModel):
    """网页模板明细结构。"""

    field_0: str | None = None  # 模板编号
    field_1: str | None = None  # 入口地址
    field_2: str | None = None  # 列表选择器
    field_3: str | None = None  # 详情选择器
    field_4: str | None = None  # 分页规则
    field_5: str | None = None  # 字段映射
    field_6: str | None = None  # 数据格式
    field_7: str | None = None  # 模板版本

class ProxyEntry(BaseModel):
    """代理节点明细结构。"""

    field_0: str | None = None  # 节点编号
    field_1: str | None = None  # 代理地址
    field_2: str | None = None  # 代理协议
    field_3: str | None = None  # 地域标签
    field_4: str | None = None  # 匿名级别
    field_5: str | None = None  # 响应速度
    field_6: str | None = None  # 存活检测间隔
    field_7: str | None = None  # 连续失败上限

class SpiderEntry(BaseModel):
    """采集作业明细结构。"""

    field_0: str | None = None  # 作业编号
    field_1: str | None = None  # 目标站点
    field_2: str | None = None  # 抽取模板
    field_3: str | None = None  # 起始地址
    field_4: str | None = None  # 并发线程数
    field_5: str | None = None  # 请求间隔秒
    field_6: str | None = None  # 每日上限条数
    field_7: str | None = None  # 超时秒数

class ScheduleEntry(BaseModel):
    """调度规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 关联站点
    field_2: str | None = None  # 调度表达式
    field_3: str | None = None  # 最大并发
    field_4: str | None = None  # 优先级
    field_5: str | None = None  # 生效时段
    field_6: str | None = None  # 执行超时
    field_7: str | None = None  # 漏触发补偿

class AntibotEntry(BaseModel):
    """对抗策略明细结构。"""

    field_0: str | None = None  # 策略编号
    field_1: str | None = None  # 验证码类型
    field_2: str | None = None  # 识别服务
    field_3: str | None = None  # 触发条件
    field_4: str | None = None  # 等待时长
    field_5: str | None = None  # 重试上限
    field_6: str | None = None  # 降级方案
    field_7: str | None = None  # 策略状态

class RatelimitEntry(BaseModel):
    """限速配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 目标站点
    field_2: str | None = None  # 每秒请求上限
    field_3: str | None = None  # 并发窗口
    field_4: str | None = None  # 令牌桶容量
    field_5: str | None = None  # 惩罚系数
    field_6: str | None = None  # 冷却秒数
    field_7: str | None = None  # 优先级权重

class RetryEntry(BaseModel):
    """重试策略明细结构。"""

    field_0: str | None = None  # 策略编号
    field_1: str | None = None  # 关联任务
    field_2: str | None = None  # 最大重试次数
    field_3: str | None = None  # 退避算法
    field_4: str | None = None  # 初始等待秒
    field_5: str | None = None  # 最大等待秒
    field_6: str | None = None  # 可重试异常
    field_7: str | None = None  # 失败转存

class ExtractorEntry(BaseModel):
    """映射规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 源字段名称
    field_2: str | None = None  # 目标字段名称
    field_3: str | None = None  # 数据类型转换
    field_4: str | None = None  # 默认填充值
    field_5: str | None = None  # 清洗表达式
    field_6: str | None = None  # 必填标识
    field_7: str | None = None  # 唯一性校验

class PipelineEntry(BaseModel):
    """流水线步骤明细结构。"""

    field_0: str | None = None  # 步骤编号
    field_1: str | None = None  # 步骤名称
    field_2: str | None = None  # 输入数据源
    field_3: str | None = None  # 处理逻辑
    field_4: str | None = None  # 输出目标
    field_5: str | None = None  # 依赖步骤
    field_6: str | None = None  # 并行度
    field_7: str | None = None  # 失败策略

class ValidatorEntry(BaseModel):
    """校验规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 校验对象
    field_2: str | None = None  # 校验类型
    field_3: str | None = None  # 校验表达式
    field_4: str | None = None  # 期望值范围
    field_5: str | None = None  # 采样比例
    field_6: str | None = None  # 异常阈值
    field_7: str | None = None  # 阻断级别

class StorageEntry(BaseModel):
    """存储配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 存储类型
    field_2: str | None = None  # 输出路径
    field_3: str | None = None  # 文件格式
    field_4: str | None = None  # 分区策略
    field_5: str | None = None  # 压缩算法
    field_6: str | None = None  # 保留天数
    field_7: str | None = None  # 覆写模式

class MonitorEntry(BaseModel):
    """监控指标明细结构。"""

    field_0: str | None = None  # 指标编号
    field_1: str | None = None  # 关联任务
    field_2: str | None = None  # 指标名称
    field_3: str | None = None  # 预警阈值
    field_4: str | None = None  # 告警级别
    field_5: str | None = None  # 统计粒度
    field_6: str | None = None  # 通知渠道
    field_7: str | None = None  # 静默窗口

class CleanseEntry(BaseModel):
    """清洗规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 目标字段
    field_2: str | None = None  # 空值替换
    field_3: str | None = None  # 格式规范化
    field_4: str | None = None  # 异常值剔除
    field_5: str | None = None  # 去重键列
    field_6: str | None = None  # 规则版本
    field_7: str | None = None  # 生效日期

class DeltaEntry(BaseModel):
    """增量游标明细结构。"""

    field_0: str | None = None  # 游标编号
    field_1: str | None = None  # 目标站点
    field_2: str | None = None  # 增量字段
    field_3: str | None = None  # 当前水位
    field_4: str | None = None  # 回退步数
    field_5: str | None = None  # 批次大小
    field_6: str | None = None  # 去重窗口
    field_7: str | None = None  # 重跑开关

class ExportEntry(BaseModel):
    """分发通道明细结构。"""

    field_0: str | None = None  # 通道编号
    field_1: str | None = None  # 输出数据集
    field_2: str | None = None  # 目标系统
    field_3: str | None = None  # 分发格式
    field_4: str | None = None  # 传输协议
    field_5: str | None = None  # 频率控制
    field_6: str | None = None  # 加密开关
    field_7: str | None = None  # 完整性校验

class RpaflowEntry(BaseModel):
    """自动化流程明细结构。"""

    field_0: str | None = None  # 流程编号
    field_1: str | None = None  # 目标页面
    field_2: str | None = None  # 操作序列
    field_3: str | None = None  # 等待条件
    field_4: str | None = None  # 超时秒数
    field_5: str | None = None  # 结果提取
    field_6: str | None = None  # 异常回滚
    field_7: str | None = None  # 执行批次

class AuthEntry(BaseModel):
    """凭证配置明细结构。"""

    field_0: str | None = None  # 凭证编号
    field_1: str | None = None  # 目标站点
    field_2: str | None = None  # 认证方式
    field_3: str | None = None  # 账号标识
    field_4: str | None = None  # 凭据有效天数
    field_5: str | None = None  # 会话保持
    field_6: str | None = None  # 刷新触发
    field_7: str | None = None  # 失效告警
