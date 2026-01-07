"""
MemCell Delete Service - 处理 MemCell 软删除逻辑

提供多种删除方式：
- 根据单个 event_id 删除
- 根据 user_id 批量删除
- 根据 group_id 批量删除
"""

from typing import Optional
from core.di.decorators import component
from core.observation.logger import get_logger
from infra_layer.adapters.out.persistence.repository.memcell_raw_repository import (
    MemCellRawRepository,
)

logger = get_logger(__name__)


@component("memcell_delete_service")
class MemCellDeleteService:
    """MemCell 软删除服务"""

    def __init__(self, memcell_repository: MemCellRawRepository):
        """
        初始化删除服务

        Args:
            memcell_repository: MemCell 数据仓库
        """
        self.memcell_repository = memcell_repository
        logger.info("MemCellDeleteService initialized")

    async def delete_by_event_id(
        self, event_id: str, deleted_by: Optional[str] = None
    ) -> bool:
        """
        根据 event_id 软删除单个 MemCell

        Args:
            event_id: MemCell 的 event_id
            deleted_by: 删除操作者标识（可选）

        Returns:
            bool: 删除成功返回 True，失败或不存在返回 False

        Example:
            >>> service = MemCellDeleteService(repo)
            >>> success = await service.delete_by_event_id("507f1f77bcf86cd799439011", "admin")
        """
        logger.info(
            "Deleting MemCell by event_id: event_id=%s, deleted_by=%s",
            event_id,
            deleted_by,
        )

        try:
            result = await self.memcell_repository.delete_by_event_id(
                event_id=event_id, deleted_by=deleted_by
            )

            if result:
                logger.info(
                    "Successfully deleted MemCell: event_id=%s, deleted_by=%s",
                    event_id,
                    deleted_by,
                )
            else:
                logger.warning("MemCell not found or already deleted: event_id=%s", event_id)

            return result

        except Exception as e:
            logger.error(
                "Failed to delete MemCell by event_id: event_id=%s, error=%s",
                event_id,
                e,
                exc_info=True,
            )
            raise

    async def delete_by_user_id(
        self, user_id: str, deleted_by: Optional[str] = None
    ) -> int:
        """
        根据 user_id 批量软删除该用户的所有 MemCell

        Args:
            user_id: 用户 ID
            deleted_by: 删除操作者标识（可选）

        Returns:
            int: 删除的记录数量

        Example:
            >>> service = MemCellDeleteService(repo)
            >>> count = await service.delete_by_user_id("user_123", "admin")
            >>> print(f"Deleted {count} records")
        """
        logger.info(
            "Deleting MemCells by user_id: user_id=%s, deleted_by=%s",
            user_id,
            deleted_by,
        )

        try:
            count = await self.memcell_repository.delete_by_user_id(
                user_id=user_id, deleted_by=deleted_by
            )

            logger.info(
                "Successfully deleted MemCells by user_id: user_id=%s, deleted_by=%s, count=%d",
                user_id,
                deleted_by,
                count,
            )

            return count

        except Exception as e:
            logger.error(
                "Failed to delete MemCells by user_id: user_id=%s, error=%s",
                user_id,
                e,
                exc_info=True,
            )
            raise

    async def delete_by_group_id(
        self, group_id: str, deleted_by: Optional[str] = None
    ) -> int:
        """
        根据 group_id 批量软删除该群组的所有 MemCell

        Args:
            group_id: 群组 ID
            deleted_by: 删除操作者标识（可选）

        Returns:
            int: 删除的记录数量

        Example:
            >>> service = MemCellDeleteService(repo)
            >>> count = await service.delete_by_group_id("group_456", "admin")
            >>> print(f"Deleted {count} records")
        """
        logger.info(
            "Deleting MemCells by group_id: group_id=%s, deleted_by=%s",
            group_id,
            deleted_by,
        )

        try:
            # 使用 repository 的 delete_many 方法
            from infra_layer.adapters.out.persistence.document.memory.memcell import (
                MemCell,
            )

            result = await MemCell.delete_many(
                {"group_id": group_id}, deleted_by=deleted_by
            )

            count = result.modified_count if result else 0

            logger.info(
                "Successfully deleted MemCells by group_id: group_id=%s, deleted_by=%s, count=%d",
                group_id,
                deleted_by,
                count,
            )

            return count

        except Exception as e:
            logger.error(
                "Failed to delete MemCells by group_id: group_id=%s, error=%s",
                group_id,
                e,
                exc_info=True,
            )
            raise

    async def delete_by_combined_criteria(
        self,
        event_id: Optional[str] = None,
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> dict:
        """
        根据组合条件删除 MemCell（多个条件同时满足）

        Args:
            event_id: MemCell 的 event_id（组合条件之一）
            user_id: 用户 ID（组合条件之一）
            group_id: 群组 ID（组合条件之一）

        Returns:
            dict: 包含删除结果的字典
                - filters: 使用的过滤条件列表
                - count: 删除的记录数量
                - success: 是否成功

        Example:
            >>> service = MemCellDeleteService(repo)
            >>> # 删除特定用户在特定群组的记录
            >>> result = await service.delete_by_combined_criteria(
            ...     user_id="user_123",
            ...     group_id="group_456",
            ... )
            >>> print(result)
            {'filters': ['user_id', 'group_id'], 'count': 5, 'success': True}
        """
        from core.oxm.constants import MAGIC_ALL
        from infra_layer.adapters.out.persistence.document.memory.memcell import MemCell

        # 构建过滤条件
        filter_dict = {}
        filters_used = []

        if event_id and event_id != MAGIC_ALL:
            from bson import ObjectId
            try:
                filter_dict["_id"] = ObjectId(event_id)
                filters_used.append("event_id")
            except Exception as e:
                logger.error("Invalid event_id format: %s, error: %s", event_id, e)
                return {
                    "filters": [],
                    "count": 0,
                    "success": False,
                    "error": f"Invalid event_id format: {event_id}",
                }

        if user_id and user_id != MAGIC_ALL:
            filter_dict["user_id"] = user_id
            filters_used.append("user_id")

        if group_id and group_id != MAGIC_ALL:
            filter_dict["group_id"] = group_id
            filters_used.append("group_id")

        # 如果没有任何过滤条件
        if not filter_dict:
            logger.warning("No deletion criteria provided (all are MAGIC_ALL)")
            return {
                "filters": [],
                "count": 0,
                "success": False,
                "error": "No deletion criteria provided",
            }

        logger.info(
            "Deleting MemCells with combined criteria: filters=%s",
            filters_used,
        )

        try:
            # 使用 delete_many 批量软删除
            result = await MemCell.delete_many(filter_dict)
            count = result.modified_count if result else 0

            logger.info(
                "Successfully deleted MemCells: filters=%s, count=%d",
                filters_used,
                count,
            )

            return {
                "filters": filters_used,
                "count": count,
                "success": count > 0,
            }

        except Exception as e:
            logger.error(
                "Failed to delete MemCells with combined criteria: filters=%s, error=%s",
                filters_used,
                e,
                exc_info=True,
            )
            raise

