from aiogram import Router
from handlers.user import router as user_router
from handlers.payment import router as payment_router
from handlers.admin import router as admin_router

main_router = Router()
main_router.include_router(admin_router)
main_router.include_router(user_router)
main_router.include_router(payment_router)
