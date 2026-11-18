import os

# https://bailian.console.aliyun.com/?tab=model#/api-key
os.environ["OPENAI_API_KEY"] = os.environ["DASHSCOPE_API_KEY"]  # 从环境变量注入，勿硬编码
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

import asyncio
from pydantic import BaseModel
from typing import Optional
from agents import Agent, InputGuardrail, GuardrailFunctionOutput, Runner
from agents.exceptions import InputGuardrailTripwireTriggered
# from agents.extensions.visualization import draw_graph
from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")
set_tracing_disabled(True)

class HomeworkOutput(BaseModel):
    """用于判断用户请求是否属于功课或学习类问题的结构"""
    is_homework: bool


# 守卫检查代理 - 》 本质也是通过大模型调用完成的
guardrail_agent = Agent(
    name="Guardrail Check Agent",
    model="qwen-max",
    instructions="判断用户的问题是否属于家庭作业、学习或教育相关问题。如果是，'is_homework'应为 True， json 返回",
    output_type=HomeworkOutput, # openai 官方推荐的一个语法，推荐大模型输出的格式类型，国内模型支持的不太好；
)

# 数学导师代理
math_tutor_agent = Agent(
    name="Math Tutor",
    model="qwen-max",
    handoff_description="负责处理所有数学问题的专家代理。",
    instructions="您是专业的数学导师。请清晰地解释每一步的推理过程，并提供具体的解题步骤和例子。",
)

# 历史导师代理
history_tutor_agent = Agent(
    name="History Tutor",
    model="qwen-max",
    handoff_description="负责处理所有历史问题的专家代理。",
    instructions="您是专业的历史學家。请協助解答歷史疑問，清晰地解釋重要事件和時代背景。",
)


async def homework_guardrail(ctx, agent, input_data):
    """
    运行检查代理来判断输入是否为功课。
    如果不是功课 ('is_homework' 为 False)，则触发阻断 (tripwire)。
    """
    print(f"\n[Guardrail Check] 正在检查输入: '{input_data}'...")
    
    # 运行检查代理
    result = await Runner.run(guardrail_agent, input_data, context=ctx.context)
    
    # 解析输出
    final_output = result.final_output_as(HomeworkOutput)#这是一个类型转换方法。它会尝试解析 result 中的内容，并将其映射到 HomeworkOutput 定义的字段（如 is_homework: bool）
    
    tripwire_triggered = not final_output.is_homework
        
    return GuardrailFunctionOutput(
        output_info=final_output,
        tripwire_triggered=tripwire_triggered,
    )


# 先进行输入的校验 guardrail_agent
# triage_agent 判断 history_tutor_agent / math_tutor_agent
# history_tutor_agent 调用
triage_agent = Agent(
    name="Triage Agent",
    model="qwen-max",
    instructions="您的任务是根据用户的功课问题内容，判断应该将请求分派给 'History Tutor' 还是 'Math Tutor'。",
    handoffs=[history_tutor_agent, math_tutor_agent],
    input_guardrails=[
        InputGuardrail(guardrail_function=homework_guardrail),
    ],
)


async def main():
    print("--- 启动中文代理系统示例 ---")
    
    print("\n" + "="*50)
    print("="*50)
    try:
        query = "请解释一下第二次世界大战爆发的主要原因是什么？"
        print(f"**用户提问:** {query}")
        result = await Runner.run(triage_agent, query) # 异步运行  guardrail agent -》 triage agent -》 math agent
        print("\n**✅ 流程通过，最终输出:**")
        print(result.final_output)
    except InputGuardrailTripwireTriggered as e:
        print("\n**❌ 守卫阻断触发:**", e)
        
    
    print("\n" + "="*50)
    print("="*50)
    try:
        query = "一个直角三角形的两条直角边分别为3和4，求斜边的长度。"
        print(f"**用户提问:** {query}")
        result = await Runner.run(triage_agent, query)
        print("\n**✅ 流程通过，最终输出:**")
        print(result.final_output)
    except InputGuardrailTripwireTriggered as e:
        print("\n**❌ 守卫阻断触发:**", e)
        
    
    print("\n" + "="*50)
    print("="*50)
    try:
        query = "你觉得明天深圳的天气怎么样？"
        print(f"**用户提问:** {query}")
        result = await Runner.run(triage_agent, query)
        print("\n**✅ 流程通过，最终输出:**")
        print(result.final_output) # 这行应该不会被执行
    except InputGuardrailTripwireTriggered as e:
        print("\n**❌ 守卫阻断触发:** 输入被阻断，因为它不是功课。")
        print(e)

if __name__ == "__main__":
    asyncio.run(main())

    # try:
    #     draw_graph(triage_agent, filename="03_基础使用")
    # except:
    #     print("绘制agent失败，默认跳过。。。")

# python3 03_基础使用案例.py 
