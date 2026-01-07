"""
MongoDB Document Base With Soft Delete

带软删除功能的文档基类，提供完整的软删除支持。
"""

from datetime import datetime
from beanie.odm.enums import SortDirection
from beanie.odm.bulk import BulkWriter
from beanie.odm.actions import ActionDirections
from beanie import DeleteRules
from pydantic import Field, BaseModel
from typing import List, Optional, Any, Mapping, Union, Tuple, Dict, Type
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.results import UpdateResult, DeleteResult

from common_utils.datetime_utils import get_now_with_timezone
from core.oxm.mongo.document_base import DocumentBase


class DocumentBaseWithSoftDelete(DocumentBase):
    """
    带软删除功能的文档基类
    
    继承自 DocumentBase，集成了完整的软删除功能：
    - 提供完整的软删除能力（自行实现，不依赖 beanie 的 DocumentWithSoftDelete）
    - 支持时区感知的 datetime 处理（来自 DocumentBase）
    - 支持数据库绑定配置（来自 DocumentBase）
    - 支持批量插入时的审计字段处理（来自 DocumentBase）
    - **扩展了删除审计字段：deleted_by（删除者）和 deleted_id（唯一性技巧）**
    - **完整的批量软删除支持**
    
    软删除字段说明：
        - deleted_at: 删除时间戳
        - deleted_by: 删除操作者标识
        - deleted_id: 删除标识ID，用于唯一索引技巧
          * 未删除时：deleted_id = 0（所有未删除文档共享此值）
          * 已删除时：deleted_id = 文档的 _id 哈希值
          * 优势：可以对 (业务字段 + deleted_id) 建立唯一索引，实现：
            - 同一业务键未删除时只能有一条记录
            - 同一业务键删除后可以有多条历史记录
            - 软删除后可以插入同样业务键的新记录
    
    核心方法：
        实例方法：
        - delete(): 软删除当前文档
        - restore(): 恢复已删除的文档
        - hard_delete(): 硬删除当前文档（物理删除）
        - is_deleted(): 检查是否已删除
        
        类方法（查询）：
        - find_one(): 查询单个文档（自动过滤已删除）
        - find_many(): 查询多个文档（自动过滤已删除）
        - hard_find_one(): 硬查询单个文档（包括已删除）
        - hard_find_many(): 硬查询多个文档（包括已删除）
        
        类方法（批量操作）：
        - delete_many(): 批量软删除
        - restore_many(): 批量恢复
        - hard_delete_many(): 批量硬删除
        
        工具方法（用于原生 pymongo API）：
        - apply_soft_delete_filter(): 应用软删除过滤条件到查询
        - get_soft_delete_filter(): 获取纯粹的软删除过滤条件
    
    重要说明：
        ⚠️ 不要使用 Model.find().delete_many()，它会执行硬删除！
        请使用 Model.delete_many(filter) 来执行批量软删除。
    
    使用示例：
        from pydantic import Field
        
        class MyDocument(DocumentBaseWithSoftDelete, AuditBase):
            email: str
            name: str
            
            class Settings:
                bind_database = "my_database"
                collection = "my_collection"
                # 唯一索引：同一邮箱未删除时只能有一条
                indexes = [
                    [("email", 1), ("deleted_id", 1)],  # 复合唯一索引
                ]
        
        # 单个软删除
        doc = await MyDocument.find_one({"email": "test@example.com"})
        await doc.delete(deleted_by="admin")  # 软删除
        
        # 批量软删除
        result = await MyDocument.delete_many(
            {"status": "inactive"}, 
            deleted_by="system"
        )
        
        # 恢复单个文档
        doc = await MyDocument.hard_find_one({"email": "test@example.com"})
        if doc and doc.is_deleted():
            await doc.restore()
        
        # 批量硬删除（谨慎使用！）
        result = await MyDocument.hard_delete_many({"is_test": True})
        
        # 使用原生 pymongo API 时应用软删除过滤
        filter_dict = MyDocument.apply_soft_delete_filter({"status": "active"})
        result = await MyDocument.get_pymongo_collection().find(filter_dict).to_list(100)
    """
    
    # 软删除相关字段
    deleted_at: Optional[datetime] = Field(default=None, description="软删除时间戳")
    deleted_by: Optional[str] = Field(default=None, description="删除操作者")
    deleted_id: int = Field(default=0, description="删除标识ID，用于唯一索引技巧")

    def is_deleted(self) -> bool:
        """
        检查文档是否已被软删除
        
        Returns:
            bool: 如果文档已删除返回 True，否则返回 False
        """
        return self.deleted_at is not None

    @classmethod
    def apply_soft_delete_filter(
        cls,
        filter_query: Optional[Mapping[str, Any]] = None,
        include_deleted: bool = False
    ) -> Dict[str, Any]:
        """
        应用软删除过滤条件到查询过滤器
        
        这是一个工具方法，用于在直接使用 get_pymongo_collection() 时手动应用软删除过滤。
        如果 filter_query 中已经包含 deleted_at 条件，则保持不变。
        如果不包含且 include_deleted=False，则添加 deleted_at=None 条件。
        
        Args:
            filter_query: 原始查询过滤条件（可选）
            include_deleted: 是否包含已删除的文档，默认 False
        
        Returns:
            Dict[str, Any]: 应用了软删除过滤的查询条件
        
        示例：
            # 场景1：使用 pymongo 原生 API 时自动过滤已删除
            filter_dict = User.apply_soft_delete_filter({"status": "active"})
            result = await User.get_pymongo_collection().find(filter_dict).to_list(100)
            
            # 场景2：需要包括已删除的文档
            filter_dict = User.apply_soft_delete_filter(
                {"status": "active"}, 
                include_deleted=True
            )
            result = await User.get_pymongo_collection().find(filter_dict).to_list(100)
            
            # 场景3：空过滤条件，只查未删除
            filter_dict = User.apply_soft_delete_filter()
            result = await User.get_pymongo_collection().find(filter_dict).to_list(100)
            
            # 场景4：使用聚合管道
            match_stage = {"$match": User.apply_soft_delete_filter({"age": {"$gt": 18}})}
            pipeline = [match_stage, {"$group": {"_id": "$city", "count": {"$sum": 1}}}]
            result = await User.get_pymongo_collection().aggregate(pipeline).to_list(100)
        """
        # 如果没有提供过滤条件，创建空字典
        if filter_query is None:
            result_filter = {}
        else:
            # 复制原始过滤条件，避免修改原始对象
            result_filter = dict(filter_query)
        
        # 如果不包含已删除的文档，且过滤条件中没有 deleted_at 字段
        if not include_deleted and "deleted_at" not in result_filter:
            result_filter["deleted_at"] = None
        
        return result_filter

    @classmethod
    def get_soft_delete_filter(cls, include_deleted: bool = False) -> Dict[str, Any]:
        """
        获取默认的软删除过滤条件
        
        这是一个简化的工具方法，返回纯粹的软删除过滤条件。
        
        Args:
            include_deleted: 是否包含已删除的文档，默认 False
        
        Returns:
            Dict[str, Any]: 软删除过滤条件，如果 include_deleted=True 则返回空字典
        
        示例：
            # 只获取未删除的过滤条件
            soft_delete_filter = User.get_soft_delete_filter()
            # 返回: {"deleted_at": None}
            
            # 获取包括已删除的过滤条件（实际返回空字典）
            all_filter = User.get_soft_delete_filter(include_deleted=True)
            # 返回: {}
            
            # 与其他条件合并使用
            my_filter = {"status": "active", **User.get_soft_delete_filter()}
            result = await User.get_pymongo_collection().find(my_filter).to_list(100)
        """
        if include_deleted:
            return {}
        return {"deleted_at": None}

    async def delete(
        self,
        session: Optional[AsyncClientSession] = None,
        bulk_writer: Optional[Any] = None,
        link_rule: Optional[Any] = None,
        skip_actions: Optional[List[Any]] = None,
        deleted_by: Optional[str] = None,
        **pymongo_kwargs: Any,
    ) -> Optional[Any]:
        """
        软删除当前文档（重写父类方法以支持 deleted_by）
        
        ⚠️ 如果文档已经被软删除，此方法会直接返回，不会修改审计字段。
        ⚠️ 直接使用 PyMongo 的 update_one 方法，完全绕过 Beanie 的 save 机制。
        
        Args:
            session: MongoDB 会话（beanie 参数）
            bulk_writer: 批量写入器（beanie 参数）
            link_rule: 链接规则（beanie 参数）
            skip_actions: 跳过的动作（beanie 参数）
            deleted_by: 删除操作者标识（可选，本类扩展参数）
            **pymongo_kwargs: 其他 pymongo 参数
        
        Returns:
            None（软删除不返回 DeleteResult）
        
        示例:
            doc = await MyDocument.find_one({"name": "test"})
            await doc.delete(deleted_by="admin")
        """
        # 检查是否已经被软删除，避免重复删除破坏审计记录
        if self.is_deleted():
            return None
        
        now = get_now_with_timezone()
        
        # deleted_id 设置为文档ID的字符串哈希值
        # 如果 id 是 ObjectId，转换为字符串后取哈希
        deleted_id_value = 0
        if self.id:
            # 将 ObjectId 转为整数（取哈希值的绝对值）
            deleted_id_value = abs(hash(str(self.id)))
        
        # 直接使用 PyMongo 的 update_one 更新数据库，完全绕过 Beanie
        await self.get_pymongo_collection().update_one(
            {"_id": self.id},
            {"$set": {
                "deleted_at": now,
                "deleted_by": deleted_by,
                "deleted_id": deleted_id_value
            }},
            session=session
        )
        
        # 更新当前对象的状态，保持一致性
        self.deleted_at = now
        self.deleted_by = deleted_by
        self.deleted_id = deleted_id_value
        
        return None

    async def restore(self, session: Optional[AsyncClientSession] = None) -> None:
        """
        恢复单个软删除的文档
        
        将当前文档的软删除标记清除，恢复为正常状态。
        
        ⚠️ 如果文档未被软删除，此方法会直接返回，不做任何操作。
        ⚠️ 直接使用 PyMongo 的 update_one 方法，完全绕过 Beanie 的 save 机制。
        
        示例：
            # 查找已删除的文档（使用 hard_find_one 可以查询包括已删除的）
            doc = await MyDocument.hard_find_one(
                {"email": "user@example.com", "deleted_at": {"$ne": None}}
            )
            
            # 恢复文档
            if doc and doc.is_deleted():
                await doc.restore()
        """
        # 如果文档未被删除，直接返回
        if not self.is_deleted():
            return
        
        # 直接使用 PyMongo 的 update_one 更新数据库，完全绕过 Beanie
        await self.get_pymongo_collection().update_one(
            {"_id": self.id},
            {"$set": {
                "deleted_at": None,
                "deleted_by": None,
                "deleted_id": 0
            }},
            session=session
        )
        
        # 更新当前对象的状态，保持一致性
        self.deleted_at = None
        self.deleted_by = None
        self.deleted_id = 0

    async def hard_delete(
        self,
        session: Optional[AsyncClientSession] = None,
        bulk_writer: Optional[BulkWriter] = None,
        link_rule: DeleteRules = DeleteRules.DO_NOTHING,
        skip_actions: Optional[List[Union[ActionDirections, str]]] = None,
        **pymongo_kwargs: Any,
    ) -> Optional[DeleteResult]:
        """
        硬删除当前文档（物理删除）
        
        ⚠️ 警告：此操作不可恢复！请谨慎使用。
        
        调用父类的 delete 方法执行真正的物理删除。
        
        Args:
            session: MongoDB 会话
            bulk_writer: 批量写入器
            link_rule: 链接规则
            skip_actions: 跳过的动作
            **pymongo_kwargs: 其他 pymongo 参数
        
        Returns:
            Optional[DeleteResult]: 删除结果
        
        示例：
            doc = await MyDocument.find_one({"name": "test"})
            await doc.hard_delete()  # 永久删除
        """
        return await super().delete(
            session=session,
            bulk_writer=bulk_writer,
            link_rule=link_rule,
            skip_actions=skip_actions,
            **pymongo_kwargs,
        )

    @classmethod
    async def delete_many(
        cls,
        filter_query: Mapping[str, Any],
        deleted_by: Optional[str] = None,
        session: Optional[AsyncClientSession] = None,
        **pymongo_kwargs: Any,
    ) -> UpdateResult:
        """
        批量软删除文档（默认的批量删除方法）
        
        将匹配的文档标记为已删除，而不是物理删除。
        这是对 beanie DocumentWithSoftDelete 缺失功能的补充。
        
        ⚠️ 注意：
        - deleted_id 在批量删除时设置为微秒级时间戳
        - 自动过滤已经被软删除的文档，避免重复删除破坏审计记录
        
        Args:
            filter_query: MongoDB 查询过滤条件
            deleted_by: 删除操作者标识（可选）
            session: 可选的 MongoDB 会话，用于事务支持
            **pymongo_kwargs: 传递给 PyMongo 的其他参数
        
        Returns:
            UpdateResult: 更新结果，包含匹配和修改的文档数量
        
        示例：
            # 批量软删除
            result = await User.delete_many(
                {"is_active": False},
                deleted_by="admin"
            )
            print(f"软删除了 {result.modified_count} 个文档")
            
            # 使用会话进行事务性软删除
            async with await client.start_session() as session:
                await User.delete_many(
                    {"status": "expired"}, 
                    deleted_by="system",
                    session=session
                )
        """
        # 设置删除时间戳
        now = get_now_with_timezone()
        
        # 注意：deleted_id 在批量删除时的处理策略
        # 由于批量操作无法高效地为每个文档设置其 _id 的哈希值，这里使用时间戳
        # 如果需要严格的唯一性约束，建议：
        # 1. 先查询出所有匹配的文档
        # 2. 逐个调用 doc.delete() 方法
        # 或者在应用层实现更复杂的批量删除逻辑
        
        update_doc = {
            "deleted_at": now,
            "deleted_by": deleted_by,
            # 使用微秒级时间戳作为 deleted_id，提供一定的唯一性
            # 对于需要严格唯一性的场景，建议使用单个删除或自定义实现
            "deleted_id": int(now.timestamp() * 1000000)  # 微秒级时间戳
        }
        
        # 应用软删除过滤：只删除未被软删除的文档，避免重复删除破坏审计记录
        final_filter = cls.apply_soft_delete_filter(filter_query, include_deleted=False)
        
        return await cls.get_pymongo_collection().update_many(
            final_filter,
            {"$set": update_doc},
            session=session,
            **pymongo_kwargs,
        )

    @classmethod
    async def restore_many(
        cls,
        filter_query: Mapping[str, Any],
        session: Optional[AsyncClientSession] = None,
        **pymongo_kwargs: Any,
    ) -> UpdateResult:
        """
        批量恢复软删除的文档
        
        将匹配的已删除文档恢复（清除所有软删除标记字段）。
        
        ⚠️ 自动只恢复已被软删除的文档，未删除的文档不会被修改。
        
        Args:
            filter_query: MongoDB 查询过滤条件
            session: 可选的 MongoDB 会话，用于事务支持
            **pymongo_kwargs: 传递给 PyMongo 的其他参数
        
        Returns:
            UpdateResult: 更新结果，包含匹配和修改的文档数量
        
        示例：
            # 恢复特定用户
            result = await User.restore_many({"email": "user@example.com"})
            
            # 恢复所有昨天删除的文档
            from datetime import timedelta
            from common_utils.datetime_utils import get_now_with_timezone
            yesterday = get_now_with_timezone() - timedelta(days=1)
            result = await User.restore_many(
                {"deleted_at": {"$gte": yesterday}}
            )
        """
        # 应用已删除过滤：只恢复已被软删除的文档
        final_filter = cls.apply_soft_delete_filter(filter_query, include_deleted=True)
        # 手动添加 deleted_at 不为 None 的条件，确保只恢复已删除的文档
        if "deleted_at" not in final_filter:
            final_filter["deleted_at"] = {"$ne": None}
        
        # 执行批量更新操作，清除所有软删除标记
        return await cls.get_pymongo_collection().update_many(
            final_filter,
            {"$set": {
                "deleted_at": None,
                "deleted_by": None,
                "deleted_id": 0
            }},
            session=session,
            **pymongo_kwargs,
        )

    @classmethod
    async def hard_delete_many(
        cls,
        filter_query: Mapping[str, Any],
        session: Optional[AsyncClientSession] = None,
        **pymongo_kwargs: Any,
    ):
        """
        批量硬删除文档（物理删除）
        
        ⚠️ 警告：此操作不可恢复！请谨慎使用。
        
        如果需要批量硬删除，也可以使用原生方式：
            await Model.find(query).delete_many()
        
        Args:
            filter_query: MongoDB 查询过滤条件
            session: 可选的 MongoDB 会话，用于事务支持
            **pymongo_kwargs: 传递给 PyMongo 的其他参数
        
        Returns:
            DeleteResult: 删除结果
        
        示例：
            # 永久删除所有测试数据
            result = await User.hard_delete_many({"is_test": True})
        """
        return await cls.get_pymongo_collection().delete_many(
            filter_query,
            session=session,
            **pymongo_kwargs,
        )

    @classmethod
    def hard_find_many(  # type: ignore
        cls,
        *args: Union[Mapping[Any, Any], bool],
        projection_model: Optional[Type[BaseModel]] = None,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
        sort: Union[None, str, List[Tuple[str, SortDirection]]] = None,
        session: Optional[AsyncClientSession] = None,
        ignore_cache: bool = True,
        fetch_links: bool = False,
        with_children: bool = False,
        lazy_parse: bool = False,
        nesting_depth: Optional[int] = None,
        nesting_depths_per_field: Optional[Dict[str, int]] = None,
        **pymongo_kwargs: Any,
    ):
        """
        硬查询多个文档（包括已软删除的）
        
        与 find_many() 不同，此方法不会过滤已删除的文档。
        用于需要查看历史记录或恢复已删除文档的场景。
        命名与 hard_delete 保持一致性。
        
        Args:
            *args: 查询条件
            projection_model: 投影模型
            skip: 跳过的文档数
            limit: 限制返回的文档数
            sort: 排序规则
            session: MongoDB 会话
            ignore_cache: 是否忽略缓存
            fetch_links: 是否获取关联文档
            with_children: 是否包含子类
            lazy_parse: 是否延迟解析
            nesting_depth: 嵌套深度
            nesting_depths_per_field: 每个字段的嵌套深度
            **pymongo_kwargs: 其他 pymongo 参数
        
        Returns:
            FindMany 查询对象
        
        示例：
            # 查找包括已删除在内的所有用户
            all_users = await User.hard_find_many({"email": "test@example.com"}).to_list()
            
            # 查找已删除的文档
            deleted_users = await User.hard_find_many(
                {"deleted_at": {"$ne": None}}
            ).to_list()
        """
        args = cls._add_class_id_filter(args, with_children)
        return cls._find_many_query_class(document_model=cls).find_many(
            *args,
            sort=sort,
            skip=skip,
            limit=limit,
            projection_model=projection_model,
            session=session,
            ignore_cache=ignore_cache,
            fetch_links=fetch_links,
            lazy_parse=lazy_parse,
            nesting_depth=nesting_depth,
            nesting_depths_per_field=nesting_depths_per_field,
            **pymongo_kwargs,
        )
    
    @classmethod
    def find_many_in_all(cls, *args, **kwargs):
        """
        已废弃：请使用 hard_find_many() 代替
        
        为了保持向后兼容性而保留，建议使用 hard_find_many()。
        """
        return cls.hard_find_many(*args, **kwargs)

    @classmethod
    def find_many(  # type: ignore
        cls,
        *args: Union[Mapping[Any, Any], bool],
        projection_model: Optional[Type[BaseModel]] = None,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
        sort: Union[None, str, List[Tuple[str, SortDirection]]] = None,
        session: Optional[AsyncClientSession] = None,
        ignore_cache: bool = False,
        fetch_links: bool = False,
        with_children: bool = False,
        lazy_parse: bool = False,
        nesting_depth: Optional[int] = None,
        nesting_depths_per_field: Optional[Dict[str, int]] = None,
        **pymongo_kwargs: Any,
    ):
        """
        查询多个文档（自动过滤已软删除的）
        
        此方法重写了父类的 find_many，自动添加 deleted_at = None 的过滤条件。
        只返回未被软删除的文档。
        
        如果需要查询包括已删除的文档，使用 hard_find_many()。
        
        Args:
            *args: 查询条件
            projection_model: 投影模型
            skip: 跳过的文档数
            limit: 限制返回的文档数
            sort: 排序规则
            session: MongoDB 会话
            ignore_cache: 是否忽略缓存
            fetch_links: 是否获取关联文档
            with_children: 是否包含子类
            lazy_parse: 是否延迟解析
            nesting_depth: 嵌套深度
            nesting_depths_per_field: 每个字段的嵌套深度
            **pymongo_kwargs: 其他 pymongo 参数
        
        Returns:
            FindMany 查询对象
        
        示例：
            # 只查找未删除的用户
            active_users = await User.find_many({"status": "active"}).to_list()
        """
        # 添加 deleted_at = None 的过滤条件
        args = cls._add_class_id_filter(args, with_children) + (
            {"deleted_at": None},
        )
        return cls._find_many_query_class(document_model=cls).find_many(
            *args,
            sort=sort,
            skip=skip,
            limit=limit,
            projection_model=projection_model,
            session=session,
            ignore_cache=ignore_cache,
            fetch_links=fetch_links,
            lazy_parse=lazy_parse,
            nesting_depth=nesting_depth,
            nesting_depths_per_field=nesting_depths_per_field,
            **pymongo_kwargs,
        )

    @classmethod
    def hard_find_one(  # type: ignore
        cls,
        *args: Union[Mapping[Any, Any], bool],
        projection_model: Optional[Type[BaseModel]] = None,
        session: Optional[AsyncClientSession] = None,
        ignore_cache: bool = True,
        fetch_links: bool = False,
        with_children: bool = False,
        nesting_depth: Optional[int] = None,
        nesting_depths_per_field: Optional[Dict[str, int]] = None,
        **pymongo_kwargs: Any,
    ):
        """
        硬查询单个文档（包括已软删除的）
        
        与 find_one() 不同，此方法不会过滤已删除的文档。
        用于需要查看历史记录或恢复已删除文档的场景。
        命名与 hard_delete 保持一致性。
        
        Args:
            *args: 查询条件
            projection_model: 投影模型
            session: MongoDB 会话
            ignore_cache: 是否忽略缓存
            fetch_links: 是否获取关联文档
            with_children: 是否包含子类
            nesting_depth: 嵌套深度
            nesting_depths_per_field: 每个字段的嵌套深度
            **pymongo_kwargs: 其他 pymongo 参数
        
        Returns:
            FindOne 查询对象
        
        示例：
            # 查找包括已删除的用户
            user = await User.hard_find_one({"email": "test@example.com"})
            
            # 查找已删除的用户并恢复
            deleted_user = await User.hard_find_one(
                {"email": "test@example.com", "deleted_at": {"$ne": None}}
            )
            if deleted_user:
                await deleted_user.restore()
        """
        args = cls._add_class_id_filter(args, with_children)
        return cls._find_one_query_class(document_model=cls).find_one(
            *args,
            projection_model=projection_model,
            session=session,
            ignore_cache=ignore_cache,
            fetch_links=fetch_links,
            nesting_depth=nesting_depth,
            nesting_depths_per_field=nesting_depths_per_field,
            **pymongo_kwargs,
        )

    @classmethod
    def find_one(  # type: ignore
        cls,
        *args: Union[Mapping[Any, Any], bool],
        projection_model: Optional[Type[BaseModel]] = None,
        session: Optional[AsyncClientSession] = None,
        ignore_cache: bool = True,
        fetch_links: bool = False,
        with_children: bool = False,
        nesting_depth: Optional[int] = None,
        nesting_depths_per_field: Optional[Dict[str, int]] = None,
        **pymongo_kwargs: Any,
    ):
        """
        查询单个文档（自动过滤已软删除的）
        
        此方法重写了父类的 find_one，自动添加 deleted_at = None 的过滤条件。
        只返回未被软删除的文档。
        
        如果需要查询包括已删除的文档，使用 hard_find_one()。
        
        Args:
            *args: 查询条件
            projection_model: 投影模型
            session: MongoDB 会话
            ignore_cache: 是否忽略缓存
            fetch_links: 是否获取关联文档
            with_children: 是否包含子类
            nesting_depth: 嵌套深度
            nesting_depths_per_field: 每个字段的嵌套深度
            **pymongo_kwargs: 其他 pymongo 参数
        
        Returns:
            FindOne 查询对象
        
        示例：
            # 查找未删除的用户
            user = await User.find_one({"email": "test@example.com"})
        """
        # 添加 deleted_at = None 的过滤条件
        args = cls._add_class_id_filter(args, with_children) + (
            {"deleted_at": None},
        )
        return cls._find_one_query_class(document_model=cls).find_one(
            *args,
            projection_model=projection_model,
            session=session,
            ignore_cache=ignore_cache,
            fetch_links=fetch_links,
            nesting_depth=nesting_depth,
            nesting_depths_per_field=nesting_depths_per_field,
            **pymongo_kwargs,
        )

    class Settings:
        """文档设置"""
        # 可以在这里设置常见的文档配置
        # 例如：索引、验证规则等


__all__ = ["DocumentBaseWithSoftDelete"]

