# OpenAPI Parser

一个用于解析 Swagger 2.0 和 OpenAPI 3.x 规范的 Python 解析器。

## 功能特性

- 支持 Swagger 2.0 和 OpenAPI 3.0/3.1/3.2
- 支持多种输入格式：dict、YAML、JSON、本地文件、URL
- 完整的 `$ref` 引用解析（本地、相对路径、file://、http://）
- 自动补全缺失的 path 参数
- 错误隔离：单个 operation 失败不会影响整个 spec 的解析
- 资源索引、依赖线索、约束标签自动生成

## 安装

```bash
pip install pyyaml packaging
```

## 快速开始

```python
from openapi_parser import OpenAPIParser

parser = OpenAPIParser()

# 从字典解析
spec = {
    "openapi": "3.0.0",
    "info": {"title": "Sample API", "version": "1.0.0"},
    "paths": {
        "/users": {
            "get": {
                "responses": {
                    "200": {"description": "Success"}
                }
            }
        }
    }
}

ir = parser.parse(spec)
print(f"Title: {ir.meta.title}")
print(f"Operations: {list(ir.operations.keys())}")
```

### 从 IR 生成选定 operations 的文档

```python
from restscope.openapi_parser import build_openapi_document

document = build_openapi_document(
    ir,
    ["GET /users", "POST /users"],
)
```

构建结果是一个 OpenAPI 3.1.0 Python 字典，只包含指定 operations。普通
Schema 会内联；递归 Schema 和 security schemes 仅保留必要的最小
`components`。callbacks 和 response links 不会输出。

构建器同时使用 Schema、Parameter、RequestBody、Response、Header、
MediaType、Example 和 SecurityScheme 节点的 `raw`。类型化 IR 字段优先：
即使其值为 `None` 或空集合，也不会回退到 raw 中的旧值。Schema raw 中
尚未类型化的 JSON Schema 关键字（例如 `multipleOf`、`contains`、
`prefixItems` 和 `unevaluatedProperties`）会递归归一化并输出。其他节点只
保留 OpenAPI 3.1 白名单内的未建模属性与 `x-*` 扩展，Swagger 2 和解析器
内部字段会被过滤。

raw 中的本地 Schema 和 Example component 引用会被内联；递归 Schema
引用会形成最小 `components.schemas` 闭包。外部引用、非对应 component
引用或缺失引用会抛出 `OperationDocumentGenerationError`。构建过程会深拷贝
raw 数据，不修改 IR。

## 使用示例

### 从文件加载

```python
ir = parser.parse("openapi.yaml")
```

### 从 URL 加载

```python
ir = parser.parse("https://example.com/openapi.yaml")
```

### 访问解析结果

```python
# 访问规范元信息
print(ir.meta.spec_format)    # openapi3.0
print(ir.meta.title)          # API 标题
print(ir.meta.version)        # API 版本

# 访问 operations
for op_key, op in ir.operations.items():
    print(f"{op.method.upper()} {op.path}")
    print(f"  Summary: {op.summary}")
    print(f"  Parameters: {len(op.path_parameters)} path, {len(op.query_parameters)} query")

# 访问 components
print(ir.components.schemas.keys())
print(ir.components.parameters.keys())

# 访问资源索引
for resource_name, resource in ir.indexes.resources.items():
    print(f"{resource_name}:")
    print(f"  Collection: {resource.collection_operations}")
    print(f"  Item: {resource.item_operations}")

# 访问约束标签
for tag in ir.indexes.constraint_tags:
    print(f"{tag.operation_key}: {tag.tag}")
```

## 输出结构

解析器输出 `OpenAPISpecIR` 对象，包含：

- `meta`: 规范元信息（标题、版本、描述等）
- `components`: 组件容器（schemas、parameters、responses 等）
- `paths`: PathItemIR 字典
- `operations`: OperationIR 字典
- `indexes`: 索引数据（operation_id 索引、资源索引、依赖线索、约束标签）
- `diagnostics`: 诊断信息（错误、警告）

## 项目结构

```
openapi_parser/
├── __init__.py          # 公共 API 导出
├── parser.py            # 主解析器
├── loader.py            # 输入加载器
├── resolver.py          # $ref 引用解析器
├── versioning.py        # 版本检测
├── validators.py        # 校验器
├── diagnostics.py       # 诊断辅助函数
├── constants.py         # 常量定义
├── ir.py                # 中间表示数据模型
├── exceptions.py        # 异常类
├── utils.py             # 工具函数
├── adapters/
│   ├── __init__.py
│   ├── base.py          # Adapter 抽象基类
│   ├── swagger2.py      # Swagger 2.0 适配器
│   ├── openapi30.py     # OpenAPI 3.0 适配器
│   ├── openapi31.py     # OpenAPI 3.1 适配器
│   └── openapi32.py     # OpenAPI 3.2 适配器
├── parsers/
│   ├── __init__.py
│   ├── meta_parser.py       # 元信息解析
│   ├── schema_parser.py     # Schema 解析
│   ├── parameter_parser.py  # 参数解析
│   ├── request_body_parser.py  # 请求体解析
│   ├── response_parser.py   # 响应解析
│   ├── security_parser.py   # 安全解析
│   ├── server_parser.py     # Server 解析
│   └── components_parser.py # 组件解析
└── postprocess/
    ├── __init__.py
    ├── resource_index.py    # 资源索引构建
    ├── value_flow.py        # 值索引/操作卡/流图构建
    └── constraint_tags.py   # 约束标签构建
```

## 运行测试

```bash
python -m pytest tests/ -v
```

## 示例

```bash
python examples/parse_example.py
```

## 注意事项

1. 解析器只负责将规范解析为中间表示，不进行：
   - 测试用例生成
   - HTTP 请求执行
   - 业务语义推理
   - 自动修复规范

2. 错误处理策略：
   - 顶层结构错误会抛出异常
   - 单个 path/operation 错误会被记录并跳过，不影响其他部分

3. Schema、Parameter、RequestBody、Response、Header、MediaType、Example
   和 SecurityScheme IR 保留 `raw` 字段；它们用于补充尚未类型化的合法属性，
   不是类型化字段的回退来源

## License

MIT
