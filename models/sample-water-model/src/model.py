"""标准水厂前馈投药量计算样板模型实现。

本模型为通用算法框架的公开样板模型，提供完全脱敏、自包含的水处理前馈投药计算参考。
业务逻辑涵盖：
1. 混凝剂投加量计算：基于进水流量与进水 COD 的化学混凝机理估算；
2. 碳源投加量计算：基于进水氨氮与反硝化碳氮比需缺量的前馈估算；
3. 异常保底截断：非负保护与物理极值截断。
"""

from __future__ import annotations

import pandas as pd


def predict(inputs: pd.DataFrame) -> pd.DataFrame:
    """计算混凝剂与碳源的前馈投加量。

    Args:
        inputs: 包含进水特征的输入数据框：
            - influent_flow: 进水瞬时流量 (m3/h)
            - influent_cod: 进水 COD 浓度 (mg/L)
            - influent_nh3n: 进水氨氮浓度 (mg/L)

    Returns:
        pd.DataFrame 包含：
            - coagulant_dosage: 混凝剂推荐投加量 (kg/h)
            - carbon_source_dosage: 复合碳源推荐投加量 (kg/h)
    """
    for required in ("influent_flow", "influent_cod", "influent_nh3n"):
        if required not in inputs.columns:
            raise ValueError(f"missing required input field: {required}")

    flow = inputs["influent_flow"].clip(lower=0.0)
    cod = inputs["influent_cod"].clip(lower=0.0)
    nh3n = inputs["influent_nh3n"].clip(lower=0.0)

    # 混凝剂投加机理：基础比率 25 mg/L，叠加 COD 负荷修正 (0.05 * COD)
    # 投加量 (kg/h) = 流量 (m3/h) * 单耗 (g/m3 或 mg/L) / 1000
    unit_coagulant = 25.0 + 0.05 * cod
    coagulant = (flow * unit_coagulant / 1000.0).clip(lower=0.0)

    # 碳源投加机理：针对脱氮碳氮比缺额，设定目标 C/N=4.5，进水自带碳源按 30% 有效利用
    # 需补 COD 差额 = 4.5 * NH3-N - 0.3 * COD (若为负则无需补碳源)
    needed_cod = (4.5 * nh3n - 0.3 * cod).clip(lower=0.0)
    # 碳源当量折算：1kg 复合碳源折合 1.2kg COD，投加量 (kg/h) = 流量 * 需补COD / (1000 * 1.2)
    carbon = (flow * needed_cod / 1200.0).clip(lower=0.0)

    return pd.DataFrame({
        "coagulant_dosage": coagulant.round(4),
        "carbon_source_dosage": carbon.round(4),
    })
